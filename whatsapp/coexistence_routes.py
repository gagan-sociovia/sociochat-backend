"""
WhatsApp Coexistence API Routes
================================

Blueprint for WhatsApp Coexistence mode features.

Endpoints:
    POST /api/whatsapp/coexistence/connect       - Connect existing WABA in coexistence mode
    GET  /api/whatsapp/coexistence/status/<id>    - Get coexistence account status
    GET  /api/whatsapp/coexistence/device/<id>    - Get device activity status
    GET  /api/whatsapp/coexistence/device/check-all - Check all accounts for inactivity
    
    GET  /api/whatsapp/coexistence/rate-limit/<id>   - Get rate limit status
    POST /api/whatsapp/coexistence/rate-limit/check  - Check if message can be sent
    
    GET  /api/whatsapp/coexistence/history/<id>     - Get history sync status
    POST /api/whatsapp/coexistence/history/<id>/complete - Mark sync complete
    
    GET  /api/whatsapp/coexistence/contacts          - Search contacts
    POST /api/whatsapp/coexistence/contacts/<id>/label - Add/remove labels
    
    GET  /api/whatsapp/coexistence/echo-messages/<id> - Get recent echo messages
    GET  /api/whatsapp/coexistence/dashboard/<id>     - Get coexistence dashboard data
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify

from models import db
from .models import (
    WhatsAppAccount,
    WhatsAppMessage,
    WhatsAppConversation,
    WhatsAppRateLimit,
    WhatsAppHistorySyncLog,
    WhatsAppContact,
)
from .coexistence_service import (
    CoexistenceRateLimiter,
    EchoHandler,
    HistorySyncHandler,
    DeviceActivityMonitor,
    ContactManager,
    classify_meta_error,
)
from .token_helper import get_account_with_token

logger = logging.getLogger(__name__)

coexistence_bp = Blueprint("coexistence", __name__)


# ============================================================
# Coexistence Connection
# ============================================================

@coexistence_bp.route("/coexistence/connect", methods=["POST"])
def connect_coexistence():
    """
    Connect an existing WhatsApp Business Account in coexistence mode.
    
    This is used when a business already has WhatsApp Business App on their phone
    and wants to also use the Cloud API without losing their existing setup.
    
    POST /api/whatsapp/coexistence/connect
    Body: {
        "code": "...",                  // Embedded Signup auth code (preferred from frontend)
        "waba_id": "...",
        "phone_number_id": "...",
        "workspace_id": "...",
        "access_token": "...",          // Optional if code provided
        "meta_business_id": "...",      // Optional
    }
    """
    data = request.get_json(silent=True) or {}
    waba_id = data.get("waba_id")
    phone_number_id = data.get("phone_number_id")
    workspace_id = data.get("workspace_id")
    access_token = data.get("access_token")
    code = data.get("code")
    meta_business_id = data.get("meta_business_id")

    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    if not access_token and not code:
        return jsonify({
            "success": False,
            "error": "code or access_token is required",
        }), 400

    try:
        if code and not access_token:
            from .oauth import exchange_embedded_signup_code
            access_token = exchange_embedded_signup_code(code)

        from .onboarding_service import finalize_whatsapp_connection

        result = finalize_whatsapp_connection(
            workspace_id=workspace_id,
            user_id=data.get("user_id"),
            access_token=access_token,
            session_waba_id=waba_id,
            session_phone_id=phone_number_id,
            token_type="long_lived",
            is_coexistence=True,
            meta_business_id=meta_business_id,
            mps_limit=5,
        )

        if result.get("error_code"):
            return jsonify(result), 409

        if result.get("success") and result.get("account", {}).get("phone_number_id"):
            CoexistenceRateLimiter.get_bucket(result["account"]["phone_number_id"])

        payload = dict(result)
        payload["coexistence"] = {
            "mps_limit": 5,
            "mode": "coexistence",
            "features": [
                "Echo messages from mobile app",
                "History sync (up to 180 days)",
                "Device activity monitoring",
                "5 MPS rate limit",
            ],
        }
        if result.get("success"):
            payload["message"] = "WhatsApp Business Account connected in coexistence mode"

        status_code = 200 if result.get("success") else 422
        return jsonify(payload), status_code

    except Exception as e:
        logger.exception(f"Coexistence connection error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Account Status
# ============================================================

@coexistence_bp.route("/coexistence/status/<int:account_id>", methods=["GET"])
def get_coexistence_status(account_id: int):
    """
    Get comprehensive coexistence status for an account.
    
    GET /api/whatsapp/coexistence/status/<id>
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    # Device activity
    device_status = DeviceActivityMonitor.get_device_status(account_id)
    
    # Rate limit
    rate_status = CoexistenceRateLimiter.get_status(account.phone_number_id)
    
    # History sync progress
    sync_logs = WhatsAppHistorySyncLog.query.filter_by(
        account_id=account_id,
    ).order_by(WhatsAppHistorySyncLog.started_at.desc()).limit(5).all()
    
    # Message stats
    total_echoes = WhatsAppMessage.query.join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.direction == "echo",
    ).count()
    
    total_outgoing = WhatsAppMessage.query.join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.direction == "outgoing",
    ).count()
    
    total_incoming = WhatsAppMessage.query.join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.direction == "incoming",
    ).count()
    
    return jsonify({
        "success": True,
        "account": account.to_dict(),
        "device": device_status,
        "rate_limit": rate_status,
        "history_sync": {
            "completed": account.history_sync_completed,
            "progress": account.history_sync_progress,
            "sync_status": account.sync_status,
            "recent_batches": [log.to_dict() for log in sync_logs],
        },
        "message_counts": {
            "echo": total_echoes,
            "outgoing": total_outgoing,
            "incoming": total_incoming,
            "total": total_echoes + total_outgoing + total_incoming,
        },
    })


# ============================================================
# Device Activity
# ============================================================

@coexistence_bp.route("/coexistence/device/<int:account_id>", methods=["GET"])
def get_device_activity(account_id: int):
    """
    Get device activity status for a coexistence account.
    
    GET /api/whatsapp/coexistence/device/<id>
    """
    status = DeviceActivityMonitor.get_device_status(account_id)
    if "error" in status:
        return jsonify({"success": False, "error": status["error"]}), 404
    
    return jsonify({"success": True, **status})


@coexistence_bp.route("/coexistence/device/check-all", methods=["GET"])
def check_all_device_activity():
    """
    Check all coexistence accounts for inactivity.
    Returns accounts that haven't shown mobile activity in 10+ days.
    
    GET /api/whatsapp/coexistence/device/check-all
    """
    alerts = DeviceActivityMonitor.check_all_accounts()
    
    return jsonify({
        "success": True,
        "inactive_accounts": alerts,
        "count": len(alerts),
        "threshold_days": DeviceActivityMonitor.INACTIVITY_THRESHOLD_DAYS,
    })


# ============================================================
# Rate Limiting
# ============================================================

@coexistence_bp.route("/coexistence/rate-limit/<int:account_id>", methods=["GET"])
def get_rate_limit_status(account_id: int):
    """
    Get current rate limit status for an account.
    
    GET /api/whatsapp/coexistence/rate-limit/<id>
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    status = CoexistenceRateLimiter.get_status(account.phone_number_id)
    
    return jsonify({
        "success": True,
        "account_id": account_id,
        "is_coexistence": account.is_coexistence,
        **status,
    })


@coexistence_bp.route("/coexistence/rate-limit/check", methods=["POST"])
def check_rate_limit():
    """
    Check if a message can be sent (pre-flight rate limit check).
    
    POST /api/whatsapp/coexistence/rate-limit/check
    Body: { "phone_number_id": "...", "count": 1 }
    """
    data = request.get_json(silent=True) or {}
    phone_number_id = data.get("phone_number_id")
    count = data.get("count", 1)
    
    if not phone_number_id:
        return jsonify({"success": False, "error": "phone_number_id is required"}), 400
    
    allowed, info = CoexistenceRateLimiter.acquire(phone_number_id, count)
    
    status_code = 200 if allowed else 429
    return jsonify({
        "success": allowed,
        "allowed": allowed,
        **info,
    }), status_code


# ============================================================
# History Sync
# ============================================================

@coexistence_bp.route("/coexistence/history/<int:account_id>", methods=["GET"])
def get_history_sync_status(account_id: int):
    """
    Get history sync status and logs for an account.
    
    GET /api/whatsapp/coexistence/history/<id>
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    logs = WhatsAppHistorySyncLog.query.filter_by(
        account_id=account_id,
    ).order_by(WhatsAppHistorySyncLog.started_at.desc()).all()
    
    total_synced = sum(log.messages_stored for log in logs)
    total_deduplicated = sum(log.messages_duplicated for log in logs)
    
    return jsonify({
        "success": True,
        "account_id": account_id,
        "completed": account.history_sync_completed,
        "progress": account.history_sync_progress,
        "sync_status": account.sync_status,
        "total_messages_synced": total_synced,
        "total_deduplicated": total_deduplicated,
        "batches": [log.to_dict() for log in logs],
    })


@coexistence_bp.route("/coexistence/history/<int:account_id>/complete", methods=["POST"])
def mark_history_complete(account_id: int):
    """
    Manually mark history sync as complete.
    
    POST /api/whatsapp/coexistence/history/<id>/complete
    """
    HistorySyncHandler.mark_sync_complete(account_id)
    
    return jsonify({
        "success": True,
        "message": "History sync marked as complete",
        "account_id": account_id,
    })


# ============================================================
# Contacts (CRM)
# ============================================================

@coexistence_bp.route("/coexistence/contacts", methods=["GET"])
def search_contacts():
    """
    Search/list contacts for an account.
    
    GET /api/whatsapp/coexistence/contacts?account_id=1&q=john&labels=vip,customer&limit=50&offset=0
    """
    account_id = request.args.get("account_id", type=int)
    if not account_id:
        return jsonify({"success": False, "error": "account_id is required"}), 400
    
    query = request.args.get("q", "")
    labels_str = request.args.get("labels", "")
    labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else None
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    
    contacts, total = ContactManager.search_contacts(
        account_id=account_id,
        query=query if query else None,
        labels=labels,
        limit=min(limit, 100),
        offset=offset,
    )
    
    return jsonify({
        "success": True,
        "contacts": [c.to_dict() for c in contacts],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@coexistence_bp.route("/coexistence/contacts/<int:contact_id>/label", methods=["POST"])
def manage_contact_label(contact_id: int):
    """
    Add or remove a label from a contact.
    
    POST /api/whatsapp/coexistence/contacts/<id>/label
    Body: { "label": "vip", "action": "add" }  // action: add|remove
    """
    data = request.get_json(silent=True) or {}
    label = data.get("label", "").strip()
    action = data.get("action", "add")
    
    if not label:
        return jsonify({"success": False, "error": "label is required"}), 400
    
    if action == "add":
        success = ContactManager.add_label(contact_id, label)
    elif action == "remove":
        success = ContactManager.remove_label(contact_id, label)
    else:
        return jsonify({"success": False, "error": "action must be 'add' or 'remove'"}), 400
    
    if not success:
        return jsonify({"success": False, "error": "Contact not found"}), 404
    
    return jsonify({"success": True, "message": f"Label '{label}' {action}ed"})


# ============================================================
# Echo Messages
# ============================================================

@coexistence_bp.route("/coexistence/echo-messages/<int:account_id>", methods=["GET"])
def get_echo_messages(account_id: int):
    """
    Get recent echo messages (sent from mobile app) for an account.
    
    GET /api/whatsapp/coexistence/echo-messages/<id>?limit=50&offset=0
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    
    echoes = WhatsAppMessage.query.join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.direction == "echo",
    ).order_by(
        WhatsAppMessage.created_at.desc()
    ).offset(offset).limit(min(limit, 100)).all()
    
    total = WhatsAppMessage.query.join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.direction == "echo",
    ).count()
    
    return jsonify({
        "success": True,
        "echo_messages": [m.to_dict() for m in echoes],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


# ============================================================
# Coexistence Dashboard
# ============================================================

@coexistence_bp.route("/coexistence/dashboard/<int:account_id>", methods=["GET"])
def get_coexistence_dashboard(account_id: int):
    """
    Get comprehensive dashboard data for a coexistence account.
    Combines all coexistence-specific data for the frontend.
    
    GET /api/whatsapp/coexistence/dashboard/<id>
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    # Device status
    device = DeviceActivityMonitor.get_device_status(account_id)
    
    # Rate limit
    rate_limit = CoexistenceRateLimiter.get_status(account.phone_number_id)
    
    # Message counts (last 7 days)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    from sqlalchemy import func
    
    direction_counts = db.session.query(
        WhatsAppMessage.direction,
        func.count(WhatsAppMessage.id),
    ).join(WhatsAppConversation).filter(
        WhatsAppConversation.account_id == account_id,
        WhatsAppMessage.created_at >= seven_days_ago,
    ).group_by(WhatsAppMessage.direction).all()
    
    counts = {d: c for d, c in direction_counts}
    
    # Active conversations count
    active_conversations = WhatsAppConversation.query.filter_by(
        account_id=account_id,
        status="open",
    ).count()
    
    # Contact count
    contact_count = WhatsAppContact.query.filter_by(
        account_id=account_id,
    ).count()
    
    # History sync summary
    sync_logs = WhatsAppHistorySyncLog.query.filter_by(
        account_id=account_id,
    ).all()
    total_synced = sum(log.messages_stored for log in sync_logs)
    
    return jsonify({
        "success": True,
        "account": account.to_dict(),
        "device": device,
        "rate_limit": rate_limit,
        "stats": {
            "messages_7d": {
                "incoming": counts.get("incoming", 0),
                "outgoing": counts.get("outgoing", 0),
                "echo": counts.get("echo", 0),
                "total": sum(counts.values()),
            },
            "active_conversations": active_conversations,
            "total_contacts": contact_count,
        },
        "history_sync": {
            "completed": account.history_sync_completed,
            "progress": account.history_sync_progress,
            "total_synced": total_synced,
        },
    })


# ============================================================
# Upgrade to Standard Cloud API
# ============================================================

@coexistence_bp.route("/coexistence/upgrade/<int:account_id>", methods=["POST"])
def upgrade_to_standard(account_id: int):
    """
    Upgrade a coexistence account to standard Cloud API.
    
    This removes the mobile dependency and increases MPS to 80-1000.
    Requires the business to migrate fully off the mobile app.
    
    POST /api/whatsapp/coexistence/upgrade/<id>
    Body: { "target_mps": 80 }
    """
    account = WhatsAppAccount.query.get(account_id)
    if not account:
        return jsonify({"success": False, "error": "Account not found"}), 404
    
    if not account.is_coexistence:
        return jsonify({
            "success": False,
            "error": "Account is already on standard Cloud API",
        }), 400
    
    data = request.get_json(silent=True) or {}
    target_mps = data.get("target_mps", 80)
    
    # Update account
    account.is_coexistence = False
    account.mps_limit = target_mps
    account.sync_status = "synced"
    
    # Update rate limit bucket
    bucket = WhatsAppRateLimit.query.get(account.phone_number_id)
    if bucket:
        bucket.max_tokens = float(target_mps)
        bucket.tokens = float(target_mps)
    
    db.session.commit()
    
    logger.info(f"Account {account_id} upgraded from coexistence to standard ({target_mps} MPS)")
    
    return jsonify({
        "success": True,
        "message": f"Account upgraded to standard Cloud API ({target_mps} MPS)",
        "account": account.to_dict(),
    })
