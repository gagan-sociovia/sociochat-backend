import secrets
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, url_for

from models import db
from .models import WhatsAppAccount
from .trigger_models import WhatsAppTrigger
from .services import WhatsAppService
from .token_helper import get_account_with_token
# decorators
from .flow_access import require_account_access

logger = logging.getLogger(__name__)

trigger_bp = Blueprint("triggers", __name__, url_prefix="/api/whatsapp")

# ============================================================
# Management Endpoints (Requires Auth)
# ============================================================

@trigger_bp.route("/accounts/<int:account_id>/triggers", methods=["GET"])
@require_account_access
def list_triggers(account_id: int, account: WhatsAppAccount, workspace_id: str):
    """List all triggers for an account."""
    try:
        triggers = WhatsAppTrigger.query.filter_by(
            account_id=account_id,
            workspace_id=workspace_id
        ).order_by(WhatsAppTrigger.created_at.desc()).all()
        
        # Enrich with variable count from templates
        from .models import WhatsAppTemplate
        templates = WhatsAppTemplate.query.filter_by(account_id=account_id).all()
        template_map = {(t.name, t.language): t.variable_count for t in templates}
        
        trigger_list = []
        for t in triggers:
            data = t.to_dict()
            # Default to 0 if template definition not found locally
            data["variable_count"] = template_map.get((t.template_name, t.language), 0)
            trigger_list.append(data)
        
        return jsonify({
            "success": True,
            "triggers": trigger_list
        })
    except Exception as e:
        logger.exception(f"Error listing triggers: {e}")
        return jsonify({"error": "Failed to list triggers"}), 500

@trigger_bp.route("/accounts/<int:account_id>/triggers", methods=["POST"])
@require_account_access
def create_trigger(account_id: int, account: WhatsAppAccount, workspace_id: str):
    """Create a new trigger."""
    data = request.get_json() or {}
    
    name = data.get("name")
    template_name = data.get("template_name")
    
    if not name or not template_name:
        return jsonify({"error": "Name and Template Name are required"}), 400
        
    try:
        # Generate slug and secret
        slug = name.lower().replace(" ", "-").replace("_", "-")
        # Ensure alphanumeric slug
        slug = "".join(c for c in slug if c.isalnum() or c == '-')
        
        secret_key = secrets.token_urlsafe(32)
        
        trigger = WhatsAppTrigger(
            workspace_id=workspace_id,
            account_id=account_id,
            name=name,
            slug=slug,
            description=data.get("description"),
            template_name=template_name,
            language=data.get("language", "en_US"),
            secret_key=secret_key,
            is_active=True
        )
        
        db.session.add(trigger)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "trigger": trigger.to_dict(),
            "message": "Trigger created successfully"
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error creating trigger: {e}")
        return jsonify({"error": str(e)}), 500

@trigger_bp.route("/accounts/<int:account_id>/triggers/<int:trigger_id>", methods=["DELETE"])
@require_account_access
def delete_trigger(account_id: int, trigger_id: int, account: WhatsAppAccount, workspace_id: str):
    """Delete a trigger."""
    try:
        trigger = WhatsAppTrigger.query.filter_by(
            id=trigger_id, 
            account_id=account_id,
            workspace_id=workspace_id
        ).first()
        
        if not trigger:
            return jsonify({"error": "Trigger not found"}), 404
            
        db.session.delete(trigger)
        db.session.commit()
        
        return jsonify({"success": True, "message": "Trigger deleted"})
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error deleting trigger: {e}")
        return jsonify({"error": "Failed to delete trigger"}), 500


# ============================================================
# Public Hook Endpoint (No Auth, uses Secret)
# ============================================================

@trigger_bp.route("/hooks/<int:trigger_id>", methods=["POST"])
def invoke_trigger(trigger_id: int):
    """
    Public endpoint to fire a trigger.
    Requires 'secret' query param or 'X-Trigger-Secret' header.
    
    Body:
    {
        "to": "1234567890",
        "variables": ["var1", "var2"] // Optional, if template has variables
    }
    """
    secret = request.args.get("secret") or request.headers.get("X-Trigger-Secret")
    data = request.get_json(silent=True) or {}
    
    if not secret:
        return jsonify({"error": "Missing secret"}), 401
        
    try:
        trigger = WhatsAppTrigger.query.get(trigger_id)
        
        if not trigger or trigger.secret_key != secret:
            # timing attack safe comparison ideally, but standard equality ok for now
            return jsonify({"error": "Invalid trigger or secret"}), 401
            
        if not trigger.is_active:
            return jsonify({"error": "Trigger is inactive"}), 400
            
        # Get recipient
        to = data.get("to")
        if not to:
             return jsonify({"error": "'to' phone number is required"}), 400
             
        # Get variables
        variables = data.get("variables", [])
        
        # Get account to send from
        account, error = get_account_with_token(trigger.account_id)
        if error or not account:
            return jsonify({"error": "Configuration error: Account not found or invalid token"}), 500
            
        # Send Message
        service = WhatsAppService(db.session, account.phone_number_id, account.get_access_token())
        
        # Determine if we need to wrap variables in components
        # Fetch template to handle named variables logic (e.g. {{name}} vs {{1}})
        from .models import WhatsAppTemplate
        template_obj = WhatsAppTemplate.query.filter_by(
            account_id=trigger.account_id, 
            name=trigger.template_name,
            language=trigger.language
        ).first()

        mapping = template_obj.get_variable_mapping() if template_obj else {}
        
        components = []
        if variables:
            body_params = []
            for idx, val in enumerate(variables):
                param = {"type": "text", "text": str(val)}
                
                # If the template uses named variables (e.g. {{customer_name}}), we MUST provide parameter_name
                # Mapping is {'1': 'customer_name', '2': 'amount'}
                pos_key = str(idx + 1)
                if pos_key in mapping:
                    param["parameter_name"] = mapping[pos_key]
                    logger.info(f"Trigger {trigger_id}: Assigned param name '{mapping[pos_key]}' to var '{val}'")
                
                body_params.append(param)

            components.append({
                "type": "body",
                "parameters": body_params
            })
            
        result = service.send_template(
            to=to,
            template_name=trigger.template_name,
            language_code=trigger.language,
            components=components
        )
        
        if result.get("success"):
            # Update stats
            trigger.trigger_count += 1
            trigger.last_triggered_at = datetime.now(timezone.utc)
            db.session.commit()
            
            logger.info(f"[Automation Source: API TRIGGER] Trigger '{trigger.name}' (ID: {trigger.id}) fired to {to}")
            
            return jsonify({
                "success": True,
                "message": "Trigger fired successfully",
                "message_id": result.get("message_id")
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to send message to WhatsApp",
                "details": result
            }), 502
            
    except Exception as e:
        logger.exception(f"Error invoking trigger {trigger_id}: {e}")
        return jsonify({"error": str(e)}), 500
