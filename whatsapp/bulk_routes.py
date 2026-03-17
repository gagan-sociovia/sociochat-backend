
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy import func, distinct

from models import db
from .models import WhatsAppAccount, WhatsAppMessage
from .drip_models import WhatsAppDripCampaign, WhatsAppDripStep, WhatsAppDripEnrollment
from .flow_access import require_account_access
from .drip_engine import process_single_enrollment, trigger_campaign_now
from .scheduler import add_campaign_job
from rate_limit.decorator import rate_limit

logger = logging.getLogger(__name__)

bulk_bp = Blueprint("bulk", __name__, url_prefix="/api/whatsapp/bulk")

@bulk_bp.route("/campaigns", methods=["GET"])
def list_campaigns():
    """List bulk messaging campaigns (trigger_type='manual')."""
    workspace_id = request.args.get("workspace_id")
    status = request.args.get("status")
    
    if not workspace_id:
        return jsonify({"success": False, "error": "Workspace ID required"}), 400
        
    query = WhatsAppDripCampaign.query.filter_by(
        workspace_id=workspace_id,
        trigger_type="manual"  # Filter for bulk campaigns
    )
    
    if status and status != "all":
        query = query.filter_by(status=status)
        
    campaigns = query.order_by(WhatsAppDripCampaign.created_at.desc()).all()
    
    result = []
    for c in campaigns:
        # Calculate stats on the fly or use cached columns
        c_dict = c.to_dict()
        
        # Add extra stats needed for bulk view
        total = c.enrolled_count
        sent = c.completed_count # Approx for now
        
        # Fetch template info from first step if available
        first_step = c.steps[0] if c.steps else None
        if first_step:
            c_dict["template_name"] = first_step.template_name
            c_dict["language"] = first_step.language
        else:
            c_dict["template_name"] = None
            c_dict["language"] = None
            
        # Map trigger_value to scheduled_at for manual campaigns
        c_dict["scheduled_at"] = c.trigger_value if c.trigger_type == "manual" else None
        
        c_dict.update({
             "total_recipients": total,
             "sent_count": sent,
             "pending_count": total - sent,
             "progress_percent": int((sent / total * 100)) if total > 0 else 0,
             "delivery_rate": 0, # TODO: Real delivery tracking
             "read_rate": 0
        })
        result.append(c_dict)
        
    return jsonify({"success": True, "campaigns": result})

@bulk_bp.route("/campaigns", methods=["POST"])
@rate_limit("whatsapp.bulk.create")
@require_account_access
def create_campaign(account: WhatsAppAccount, workspace_id: str):
    """Create a new bulk campaign."""
    data = request.get_json() or {}
    account_id = account.id
    name = data.get("name")
    
    print(f"DEBUG: create_campaign payload: {data}")
    
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
        
    template_name = data.get("template_name")
    if not template_name:
        return jsonify({"success": False, "error": "Template is required for bulk campaigns"}), 400
        
    # Create campaign
    campaign = WhatsAppDripCampaign(
        workspace_id=workspace_id,
        account_id=account_id,
        name=name,
        description=data.get("description", ""),
        trigger_type="manual",
        status="draft",
        created_at=datetime.now(timezone.utc)
    )
    
    db.session.add(campaign)
    db.session.flush() # Get ID
    
    # If template is selected, add as Step 1
    template_name = data.get("template_name")
    if template_name:
        step = WhatsAppDripStep(
            campaign_id=campaign.id,
            step_order=1,
            template_name=template_name,
            language=data.get("template_language", "en_US"),
            delay_seconds=0
        )
        db.session.add(step)
        
    db.session.commit()
    
    return jsonify({"success": True, "campaign": campaign.to_dict()})

@bulk_bp.route("/campaigns/<int:campaign_id>", methods=["GET"])
def get_campaign(campaign_id: int):
    """Get single bulk campaign details."""
    workspace_id = request.args.get("workspace_id")
    
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    return jsonify({"success": True, "campaign": campaign.to_dict()})

@bulk_bp.route("/campaigns/<int:campaign_id>/stats", methods=["GET"])
def get_campaign_stats(campaign_id: int):
    """Get live stats for a campaign."""
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    # Simple stats for now based on enrollment table
    total = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id).count()
    completed = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="completed").count()
    failed = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="failed").count()
    active = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="active").count()
    
    # Real stats from messages table (Unique Users)
    # Delivered includes 'delivered' and 'read' status
    delivered = db.session.query(func.count(distinct(WhatsAppMessage.conversation_id)))\
        .filter(WhatsAppMessage.campaign_id == campaign_id, WhatsAppMessage.status.in_(["delivered", "read"]))\
        .scalar() or 0
    
    read = db.session.query(func.count(distinct(WhatsAppMessage.conversation_id)))\
        .filter(WhatsAppMessage.campaign_id == campaign_id, WhatsAppMessage.status == "read")\
        .scalar() or 0

    stats = {
        "total_recipients": total,
        "sent": completed,
        "failed": failed,
        "pending": active, 
        "queued": 0,
        "delivered": delivered,
        "read": read,
        "progress_percent": int((completed / total * 100)) if total > 0 else 0,
        "delivery_rate": int((delivered / completed * 100)) if completed > 0 else 0,
        "read_rate": int((read / completed * 100)) if completed > 0 else 0,
        "failure_rate": int((failed / total * 100)) if total > 0 else 0
    }
    
    return jsonify({"success": True, "stats": stats})

@bulk_bp.route("/crm-audience", methods=["GET"])
def get_crm_audience():
    """Fetch CRM audience (Leads or Contacts) for bulk messaging."""
    workspace_id = request.args.get("workspace_id")
    source = request.args.get("source", "leads") # leads or contacts
    search = request.args.get("search")
    
    if not workspace_id:
        return jsonify({"success": False, "error": "Workspace ID required"}), 400
        
    # Access dynamic CRM models
    from flask import current_app
    crm_models = getattr(current_app, "crm_models", None)
    
    if not crm_models:
        return jsonify({"success": False, "error": "CRM models not initialized"}), 500
        
    model = crm_models["Lead"] if source == "leads" else crm_models["Contact"]
    
    query = model.query.filter_by(workspace_id=workspace_id)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            db.or_(
                model.name.ilike(search_term),
                model.phone.ilike(search_term),
                model.email.ilike(search_term)
            )
        )
        
    # Fetch ALL records (no limit as requested)
    records = query.order_by(model.created_at.desc()).all()
    
    audience = []
    
    # Summary stats
    total = len(records)
    with_phone = 0
    whatsapp_ready = 0
    
    for r in records:
        # Normalize phone
        raw_phone = r.phone or ""
        norm_phone = "".join(filter(str.isdigit, str(raw_phone)))
        
        # Basic validation (at least 10 digits)
        is_valid = len(norm_phone) >= 10
        
        if raw_phone:
            with_phone += 1
            if is_valid:
                whatsapp_ready += 1
        
        audience.append({
            "id": r.id,
            "name": r.name,
            "phone": raw_phone,
            "phone_normalized": norm_phone if is_valid else None,
            "email": r.email,
            "company": r.company,
            "status": r.status,
            "source": getattr(r, "source", None) or getattr(r, "external_source", None),
            "whatsapp_ready": is_valid,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
        
    return jsonify({
        "success": True, 
        "audience": audience,
        "summary": {
            "total": total,
            "with_phone": with_phone,
            "whatsapp_ready": whatsapp_ready
        }
    })
@bulk_bp.route("/campaigns/<int:campaign_id>/recipients", methods=["POST"])
@rate_limit("whatsapp.bulk.recipients")
def add_recipients(campaign_id: int):
    """Add recipients to a bulk campaign."""
    workspace_id = request.args.get("workspace_id")
    data = request.get_json() or {}
    recipients = data.get("recipients", [])
    
    if not recipients:
        return jsonify({"success": False, "error": "No recipients provided"}), 400
        
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    added_count = 0
    duplicates_count = 0
    invalid_count = 0
    
    for r in recipients:
        phone = r.get("phone_number")
        if not phone:
            invalid_count += 1
            continue
            
        # Basic validation: strip non-digits and check length
        clean_phone = "".join(filter(str.isdigit, str(phone)))
        if len(clean_phone) < 10:
            invalid_count += 1
            continue
            
        # Check for duplicate in this campaign
        existing = WhatsAppDripEnrollment.query.filter_by(
            campaign_id=campaign_id,
            phone_number=clean_phone
        ).first()
        
        if existing:
            # Update variables if needed
            existing.variables = r.get("params", {})
            existing.name = r.get("name")
            duplicates_count += 1
        else:
            enrollment = WhatsAppDripEnrollment(
                campaign_id=campaign_id,
                phone_number=phone,
                current_step_order=0,
                status="active",
                variables=r.get("params", {}),
                created_at=datetime.now(timezone.utc),
                next_run_at=datetime.now(timezone.utc)
            )
            db.session.add(enrollment)
            added_count += 1
            
    db.session.commit()
    
    # Update stats
    campaign.enrolled_count = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id).count()
    db.session.commit()
    
    return jsonify({
        "success": True, 
        "added": added_count, 
        "duplicates": duplicates_count, 
        "invalid": invalid_count
    })

@bulk_bp.route("/campaigns/<int:campaign_id>/schedule", methods=["POST"])
@rate_limit("whatsapp.bulk.schedule")
def schedule_campaign(campaign_id: int):
    """Schedule or send a campaign."""
    workspace_id = request.args.get("workspace_id") or (request.get_json() or {}).get("workspace_id")
    data = request.get_json() or {}
    
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": f"Access denied. Campaign WS: {campaign.workspace_id}, Req WS: {workspace_id}"}), 403
        
    scheduled_at_str = data.get("scheduled_at")
    
    if scheduled_at_str:
        try:
             # Parse ISO format
            scheduled_at = datetime.fromisoformat(scheduled_at_str.replace('Z', '+00:00'))
            # If naive, assume UTC (or handle timezone awareness properly based on app config)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
                
            # If scheduling for future
            campaign.status = "scheduled"
            # Store scheduled_at in trigger_value (repurposing unused field for manual campaigns)
            campaign.trigger_value = scheduled_at.isoformat()
            
            # Schedule the job
            job_id = add_campaign_job(campaign.id, scheduled_at, trigger_campaign_now)
            if not job_id:
                return jsonify({"success": False, "error": "Failed to schedule job"}), 500
                
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format"}), 400
    else:
        # Send now -> Set status to 'running' immediately so UI shows correct state
        campaign.status = "running"
        campaign.trigger_value = None
        
        from pytz import utc
        from datetime import timedelta
        # Schedule 1 second from now to process immediately
        send_time = datetime.now(utc) + timedelta(seconds=1)
        add_campaign_job(campaign.id, send_time, trigger_campaign_now)
        logger.info(f"Send Now: Campaign {campaign.id} set to RUNNING, job scheduled for {send_time}")

        
    db.session.commit()
    
    # TODO: Trigger background job to start sending if 'running'
    
    return jsonify({"success": True, "status": campaign.status})

@bulk_bp.route("/campaigns/<int:campaign_id>/pause", methods=["POST"])
def pause_campaign(campaign_id: int):
    """Pause a running campaign."""
    workspace_id = request.args.get("workspace_id") or (request.get_json() or {}).get("workspace_id")
    
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    if campaign.status == "running":
        campaign.status = "paused"
        db.session.commit()
        
    return jsonify({"success": True, "status": campaign.status})

@bulk_bp.route("/campaigns/<int:campaign_id>/resume", methods=["POST"])
def resume_campaign(campaign_id: int):
    """Resume a paused campaign."""
    workspace_id = request.args.get("workspace_id") or (request.get_json() or {}).get("workspace_id")
    
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    if campaign.status == "paused":
        campaign.status = "running"
        db.session.commit()
        
    return jsonify({"success": True, "status": campaign.status})

@bulk_bp.route("/campaigns/<int:campaign_id>", methods=["DELETE"])
def delete_campaign(campaign_id: int):
    """Delete a campaign."""
    workspace_id = request.args.get("workspace_id") or (request.get_json() or {}).get("workspace_id")
    force = request.args.get("force") == "true"
    
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    if workspace_id and str(campaign.workspace_id) != str(workspace_id):
        return jsonify({"success": False, "error": "Access denied"}), 403
        
    if campaign.status == "running" and not force:
        return jsonify({"success": False, "error": "Cannot delete running campaign. Stop it first or use force=true"}), 400
        
    # Delete enrollments first
    WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id).delete()
    
    # Delete steps
    WhatsAppDripStep.query.filter_by(campaign_id=campaign_id).delete()
    
    db.session.delete(campaign)
    db.session.commit()
    
    return jsonify({"success": True, "message": "Campaign deleted"})
