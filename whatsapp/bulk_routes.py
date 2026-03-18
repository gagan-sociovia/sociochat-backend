
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy import func, distinct, case

from models import db
from .models import WhatsAppAccount, WhatsAppMessage, WhatsAppConversation
from .drip_models import WhatsAppDripCampaign, WhatsAppDripStep, WhatsAppDripEnrollment
from .flow_access import require_account_access
from .drip_engine import process_single_enrollment, trigger_campaign_now
from .scheduler import add_campaign_job
from .utils import normalize_phone_robust
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
    
    # Batch-fetch message status counts for all campaign IDs
    campaign_ids = [c.id for c in campaigns]
    msg_stats_query = db.session.query(
        WhatsAppMessage.campaign_id,
        WhatsAppMessage.status,
        func.count(distinct(WhatsAppMessage.conversation_id))
    ).filter(
        WhatsAppMessage.campaign_id.in_(campaign_ids)
    ).group_by(WhatsAppMessage.campaign_id, WhatsAppMessage.status).all()
    
    # Build {campaign_id: {status: count}} lookup
    campaign_msg_counts = {}
    for cid, status_val, cnt in msg_stats_query:
        campaign_msg_counts.setdefault(cid, {})[status_val] = cnt
    
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
        
        # Compute real delivery/read rates from message statuses
        counts = campaign_msg_counts.get(c.id, {})
        delivered = counts.get("delivered", 0) + counts.get("read", 0)
        read = counts.get("read", 0)
        failed = counts.get("failed", 0)
        
        c_dict.update({
             "total_recipients": total,
             "sent_count": sent,
             "pending_count": total - sent,
             "progress_percent": int((sent / total * 100)) if total > 0 else 0,
             "delivery_rate": int((delivered / sent * 100)) if sent > 0 else 0,
             "read_rate": int((read / sent * 100)) if sent > 0 else 0,
             "failed_count": failed,
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
    
    # Enrollment-level stats
    total = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id).count()
    completed = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="completed").count()
    enrollment_failed = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="failed").count()
    active = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="active").count()
    blocked = WhatsAppDripEnrollment.query.filter_by(campaign_id=campaign_id, status="blocked_missing_data").count()
    
    # Real stats from messages table — counts actual delivery outcomes from webhooks
    msg_stats = db.session.query(
        WhatsAppMessage.status,
        func.count(distinct(WhatsAppMessage.conversation_id))
    ).filter(
        WhatsAppMessage.campaign_id == campaign_id
    ).group_by(WhatsAppMessage.status).all()
    
    msg_counts = {s: c for s, c in msg_stats}
    
    # Message-level delivery failures (from Meta webhook, e.g. error 130472, 131049)
    msg_failed = msg_counts.get("failed", 0)
    # Delivered = delivered + read (read implies delivered)
    delivered = msg_counts.get("delivered", 0) + msg_counts.get("read", 0)
    read = msg_counts.get("read", 0)
    
    # Total failed = enrollment-level failures + message-level delivery failures
    total_failed = enrollment_failed + blocked + msg_failed
    # Sent = completed enrollments (API accepted the message)
    sent = completed

    stats = {
        "total_recipients": total,
        "sent": sent,
        "failed": total_failed,
        "pending": active, 
        "queued": 0,
        "delivered": delivered,
        "read": read,
        "progress_percent": int((completed / total * 100)) if total > 0 else 0,
        "delivery_rate": int((delivered / sent * 100)) if sent > 0 else 0,
        "read_rate": int((read / sent * 100)) if sent > 0 else 0,
        "failure_rate": int((total_failed / total * 100)) if total > 0 else 0
    }
    
    return jsonify({"success": True, "stats": stats})


@bulk_bp.route("/campaigns/<int:campaign_id>/failed", methods=["GET"])
def get_campaign_failed_messages(campaign_id: int):
    """Get details of failed message deliveries for a campaign."""
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    
    # Get messages that failed delivery (webhook reported failure)
    failed_messages = db.session.query(
        WhatsAppMessage.id,
        WhatsAppMessage.error_code,
        WhatsAppMessage.error_message,
        WhatsAppMessage.created_at,
        WhatsAppConversation.user_phone
    ).join(
        WhatsAppConversation, WhatsAppMessage.conversation_id == WhatsAppConversation.id
    ).filter(
        WhatsAppMessage.campaign_id == campaign_id,
        WhatsAppMessage.status == "failed"
    ).all()
    
    # Get enrollments that failed at enrollment level
    failed_enrollments = WhatsAppDripEnrollment.query.filter(
        WhatsAppDripEnrollment.campaign_id == campaign_id,
        WhatsAppDripEnrollment.status.in_(["failed", "blocked_missing_data"])
    ).all()
    
    failures = []
    for msg in failed_messages:
        failures.append({
            "phone": msg.user_phone,
            "error_code": msg.error_code,
            "error_message": msg.error_message,
            "type": "delivery_failed",
            "timestamp": msg.created_at.isoformat() + "Z" if msg.created_at else None
        })
    for enr in failed_enrollments:
        failures.append({
            "phone": enr.phone_number,
            "error_code": None,
            "error_message": enr.status_reason or enr.status,
            "type": "enrollment_failed",
            "timestamp": None
        })
    
    return jsonify({"success": True, "failures": failures})

@bulk_bp.route("/campaigns/<int:campaign_id>/resubscribe-webhooks", methods=["POST"])
def resubscribe_campaign_webhooks(campaign_id: int):
    """Re-subscribe WABA webhooks for a campaign's account. 
    
    Use this to fix webhook delivery issues when status updates stop arriving.
    """
    campaign = WhatsAppDripCampaign.query.get_or_404(campaign_id)
    account = WhatsAppAccount.query.get(campaign.account_id)
    
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    access_token = account.get_access_token()
    if not access_token:
        return jsonify({"success": False, "error": "No access token for account"}), 400
    
    from .health_check import subscribe_waba_to_webhooks
    success, message, details = subscribe_waba_to_webhooks(account.waba_id, access_token)
    
    return jsonify({
        "success": success,
        "message": message,
        "details": details,
        "waba_id": account.waba_id,
        "account_phone": account.display_phone_number,
    }), 200 if success else 400


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
        # Normalize phone using robust normalizer
        raw_phone = r.phone or ""
        norm_phone = normalize_phone_robust(raw_phone)
        
        # Valid if normalizer returned a result
        is_valid = norm_phone is not None
        
        if raw_phone:
            with_phone += 1
            if is_valid:
                whatsapp_ready += 1
        
        audience.append({
            "id": r.id,
            "name": r.name,
            "phone": raw_phone,
            "phone_normalized": norm_phone,
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
            
        # Robust normalization: handles multi-number, country codes, etc.
        clean_phone = normalize_phone_robust(phone)
        if not clean_phone:
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
                phone_number=clean_phone,
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
