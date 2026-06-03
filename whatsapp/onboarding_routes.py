"""
Local onboarding test helpers — configure Meta webhooks and debug onboarding state.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from .models import WhatsAppAccount
from .onboarding_service import build_health_payload, revalidate_account

onboarding_test_bp = Blueprint("whatsapp_onboarding_test", __name__)


def _public_base_url() -> str:
    """Public URL Meta uses for webhooks (tunnel URL when testing locally)."""
    return (
        os.getenv("WEBHOOK_PUBLIC_URL")
        or os.getenv("APP_BASE_URL")
        or "http://localhost:5000"
    ).rstrip("/")


@onboarding_test_bp.route("/onboarding/local-setup", methods=["GET"])
def local_setup():
    """
    GET /api/whatsapp/onboarding/local-setup

    Returns URLs and env checklist for local Meta + webhook testing.
    """
    app_id = os.getenv("FB_APP_ID") or os.getenv("META_APP_ID")
    app_secret = bool(os.getenv("FB_APP_SECRET") or os.getenv("META_APP_SECRET"))
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    public_base = _public_base_url()
    is_localhost = "localhost" in public_base or "127.0.0.1" in public_base

    return jsonify({
        "ready_for_meta_connect": bool(app_id and app_secret),
        "webhook_public_url": f"{public_base}/api/whatsapp/webhook",
        "oauth_callback_url": f"{public_base}/api/whatsapp/connect/callback",
        "health_url_template": f"{public_base}/api/whatsapp/health?workspace_id=YOUR_WORKSPACE_ID",
        "verify_token_set": bool(verify_token and verify_token != "your-webhook-verify-token"),
        "verify_token_hint": verify_token if verify_token else None,
        "app_id": app_id,
        "is_localhost": is_localhost,
        "webhook_warning": (
            "Meta cannot reach localhost. Run scripts/start-local-webhook.ps1 and set "
            "WEBHOOK_PUBLIC_URL in .env to the ngrok HTTPS URL, then update Meta App Dashboard."
            if is_localhost
            else None
        ),
        "meta_dashboard_steps": [
            "developers.facebook.com → Your App → WhatsApp → Configuration",
            f"Callback URL: {public_base}/api/whatsapp/webhook",
            f"Verify token: {verify_token or '(set WHATSAPP_VERIFY_TOKEN in .env)'}",
            "Subscribe to: messages, message_template_status_update (and others as needed)",
            "Embedded Signup → use VITE_WHATSAPP_CONFIG_ID on frontend",
        ],
    })


@onboarding_test_bp.route("/onboarding/revalidate", methods=["POST"])
def manual_revalidate():
    """
    POST /api/whatsapp/onboarding/revalidate
    Body or query: workspace_id or account_id — force revalidation (local testing).
    """
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id") or request.args.get("workspace_id")
    account_id = data.get("account_id") or request.args.get("account_id", type=int)

    if account_id:
        account = WhatsAppAccount.query.get(account_id)
    elif workspace_id:
        account = WhatsAppAccount.query.filter_by(workspace_id=workspace_id).order_by(
            WhatsAppAccount.id.desc()
        ).first()
    else:
        return jsonify({"success": False, "error": "workspace_id or account_id required"}), 400

    if not account:
        return jsonify({"success": False, "error": "No WhatsApp account found"}), 404

    validation = revalidate_account(account, persist=True)
    return jsonify({
        "success": validation.ok,
        "health": build_health_payload(account, validation),
        "account": account.to_dict(),
    })


@onboarding_test_bp.route("/onboarding/accounts", methods=["GET"])
def list_onboarding_accounts():
    """GET /api/whatsapp/onboarding/accounts — all accounts with onboarding state (local debug)."""
    if os.getenv("FLASK_ENV") == "production" and not os.getenv("ALLOW_ONBOARDING_DEBUG"):
        return jsonify({"error": "Not available in production"}), 403

    accounts = WhatsAppAccount.query.order_by(WhatsAppAccount.id.desc()).limit(50).all()
    return jsonify({
        "accounts": [
            {
                "id": a.id,
                "workspace_id": a.workspace_id,
                "waba_id": a.waba_id,
                "phone_number_id": a.phone_number_id,
                "display_phone_number": a.display_phone_number,
                "is_active": a.is_active,
                "onboarding_status": a.onboarding_status,
                "onboarding_error": a.onboarding_error,
                "app_subscribed": a.app_subscribed,
                "last_validation_at": a.last_validation_at.isoformat() if a.last_validation_at else None,
            }
            for a in accounts
        ]
    })
