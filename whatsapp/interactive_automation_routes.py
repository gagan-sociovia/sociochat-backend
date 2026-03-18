"""
Interactive Automation API Routes
=================================

REST API for managing Interactive WhatsApp automation flows (visual builder).

Endpoints:
    GET    /api/whatsapp/interactive-automations          → List all automations
    POST   /api/whatsapp/interactive-automations          → Create new automation
    GET    /api/whatsapp/interactive-automations/<id>     → Get automation by ID
    PUT    /api/whatsapp/interactive-automations/<id>     → Update automation
    DELETE /api/whatsapp/interactive-automations/<id>     → Delete automation
    POST   /api/whatsapp/interactive-automations/<id>/publish → Publish automation
    POST   /api/whatsapp/interactive-automations/<id>/pause   → Pause automation
"""

import logging
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, g
from models import db
from .visual_automation_models import WhatsAppVisualAutomation
from .models import WhatsAppAccount

logger = logging.getLogger(__name__)

interactive_automation_bp = Blueprint("interactive_automation", __name__)


# ============================================================
# Decorators
# ============================================================

def require_workspace(f):
    """
    Decorator to verify workspace_id is provided.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        workspace_id = request.args.get("workspace_id") or request.json.get("workspace_id")
        if not workspace_id:
            return jsonify({"success": False, "error": "workspace_id is required"}), 400
        kwargs["workspace_id"] = str(workspace_id)
        return f(*args, **kwargs)
    return decorated_function


# ============================================================
# Interactive Automation CRUD
# ============================================================

@interactive_automation_bp.route("/interactive-automations", methods=["GET"])
@require_workspace
def list_interactive_automations(workspace_id: str):
    """
    List all interactive automations for a workspace.
    
    Query params:
        - workspace_id: Required workspace ID
        - account_id: Optional filter by account
        - status: Optional filter by status (draft, active, paused)
    """
    try:
        account_id = request.args.get("account_id", type=int)
        status = request.args.get("status")
        
        query = WhatsAppVisualAutomation.query.filter_by(workspace_id=workspace_id)
        
        if account_id:
            query = query.filter_by(account_id=account_id)
        if status:
            query = query.filter_by(status=status)
            
        automations = query.order_by(WhatsAppVisualAutomation.updated_at.desc()).all()
        
        return jsonify({
            "success": True,
            "automations": [a.to_dict() for a in automations]
        })
        
    except Exception as e:
        logger.error(f"Error listing interactive automations: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations", methods=["POST"])
def create_interactive_automation():
    """
    Create a new interactive automation.
    
    Request body:
    {
        "account_id": 123,
        "workspace_id": "abc",
        "name": "My Automation",
        "description": "Optional description",
        "nodes": [...],
        "edges": [...],
        "trigger": {"type": "any_reply", "enabled": true}
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400
            
        workspace_id = data.get("workspace_id")
        account_id = data.get("account_id")
        name = data.get("name", "Untitled Automation")
        
        if not workspace_id:
            return jsonify({"success": False, "error": "workspace_id is required"}), 400
        if not account_id:
            return jsonify({"success": False, "error": "account_id is required"}), 400
            
        # Verify account exists
        account = WhatsAppAccount.query.get(account_id)
        if not account:
            return jsonify({"success": False, "error": "Account not found"}), 404
            
        # Extract trigger info
        trigger = data.get("trigger", {})
        trigger_type = trigger.get("type", "any_reply")
        
        # Create automation
        automation = WhatsAppVisualAutomation(
            workspace_id=str(workspace_id),
            account_id=account_id,
            name=name,
            description=data.get("description"),
            trigger_type=trigger_type,
            trigger_config=trigger,
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            status="draft",
            is_active=False,
        )
        
        db.session.add(automation)
        db.session.commit()
        
        logger.info(f"Created interactive automation {automation.id} for workspace {workspace_id}")
        
        return jsonify({
            "success": True,
            "automation": automation.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/<int:automation_id>", methods=["GET"])
def get_interactive_automation(automation_id: int):
    """Get a specific interactive automation."""
    try:
        automation = WhatsAppVisualAutomation.query.get(automation_id)
        
        if not automation:
            return jsonify({"success": False, "error": "Automation not found"}), 404
            
        return jsonify({
            "success": True,
            "automation": automation.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error getting interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/<int:automation_id>", methods=["PUT"])
def update_interactive_automation(automation_id: int):
    """
    Update an interactive automation.
    
    Only provided fields are updated.
    """
    try:
        automation = WhatsAppVisualAutomation.query.get(automation_id)
        
        if not automation:
            return jsonify({"success": False, "error": "Automation not found"}), 404
            
        data = request.get_json()
        
        # Update fields if provided
        if "name" in data:
            automation.name = data["name"]
        if "description" in data:
            automation.description = data["description"]
        if "nodes" in data:
            automation.nodes = data["nodes"]
        if "edges" in data:
            automation.edges = data["edges"]
        if "trigger" in data:
            trigger = data["trigger"]
            automation.trigger_type = trigger.get("type", automation.trigger_type)
            automation.trigger_config = trigger
        if "viewport" in data:
            automation.viewport = data["viewport"]
            
        automation.version = (automation.version or 1) + 1
        automation.updated_at = datetime.now(timezone.utc)
        
        db.session.commit()
        
        logger.info(f"Updated interactive automation {automation_id}")
        
        return jsonify({
            "success": True,
            "automation": automation.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/<int:automation_id>", methods=["DELETE"])
def delete_interactive_automation(automation_id: int):
    """Delete an interactive automation."""
    try:
        automation = WhatsAppVisualAutomation.query.get(automation_id)
        
        if not automation:
            return jsonify({"success": False, "error": "Automation not found"}), 404
            
        db.session.delete(automation)
        db.session.commit()
        
        logger.info(f"Deleted interactive automation {automation_id}")
        
        return jsonify({
            "success": True,
            "message": "Automation deleted successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/<int:automation_id>/publish", methods=["POST"])
def publish_interactive_automation(automation_id: int):
    """
    Publish an interactive automation.
    
    Sets status to 'active' and is_active to true.
    """
    try:
        automation = WhatsAppVisualAutomation.query.get(automation_id)
        
        if not automation:
            return jsonify({"success": False, "error": "Automation not found"}), 404
            
        automation.activate()
        db.session.commit()
        
        logger.info(f"Published interactive automation {automation_id}")
        
        return jsonify({
            "success": True,
            "automation": automation.to_dict(),
            "message": "Automation published successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error publishing interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/<int:automation_id>/pause", methods=["POST"])
def pause_interactive_automation(automation_id: int):
    """
    Pause an interactive automation.
    
    Sets status to 'paused' and is_active to false.
    """
    try:
        automation = WhatsAppVisualAutomation.query.get(automation_id)
        
        if not automation:
            return jsonify({"success": False, "error": "Automation not found"}), 404
            
        automation.pause()
        db.session.commit()
        
        logger.info(f"Paused interactive automation {automation_id}")
        
        return jsonify({
            "success": True,
            "automation": automation.to_dict(),
            "message": "Automation paused successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error pausing interactive automation: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@interactive_automation_bp.route("/interactive-automations/clear-states", methods=["GET", "POST"])
def clear_conversation_states():
    """
    Clear all active conversation states.
    
    Useful for testing and debugging - clears stale flow states.
    
    Query params:
        - workspace_id: Optional workspace ID
        - phone: Optional phone number to clear states for
    """
    try:
        from .visual_automation_models import WhatsAppConversationState
        
        # Get params from query string or JSON body (if present)
        workspace_id = request.args.get("workspace_id")
        phone = request.args.get("phone")
        
        # Try to get from JSON body if not in query params
        if request.is_json:
            data = request.get_json() or {}
            workspace_id = workspace_id or data.get("workspace_id")
            phone = phone or data.get("phone")
        
        if not workspace_id:
            # Clear ALL active states (admin/debug use)
            query = WhatsAppConversationState.query.filter_by(is_active=True)
        else:
            query = WhatsAppConversationState.query.filter_by(
                workspace_id=str(workspace_id),
                is_active=True
            )
        
        if phone:
            query = query.filter_by(phone_number=phone)
        
        states = query.all()
        count = len(states)
        
        for state in states:
            state.is_active = False
        
        db.session.commit()
        
        logger.info(f"Cleared {count} conversation states")
        
        return jsonify({
            "success": True,
            "cleared_count": count,
            "message": f"Cleared {count} active conversation states"
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing conversation states: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
