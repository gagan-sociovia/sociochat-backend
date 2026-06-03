"""
WhatsApp Tech Provider onboarding orchestration — validation gate before ACTIVE.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models import db

from .connection_guard import check_phone_available
from .meta_asset_discovery import (
    BindingResult,
    discover_whatsapp_assets,
    resolve_binding_for_auto_connect,
    validate_discovered_assets,
)
from .onboarding_status import OnboardingStatus, user_message_for_status
from .models import WhatsAppAccount
from .utils import subscribe_waba_to_app, verify_waba_app_subscription

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def apply_validation_to_account(account: WhatsAppAccount, validation) -> None:
    """Persist onboarding fields from a ValidationResult."""
    account.onboarding_status = validation.status
    account.onboarding_error = validation.onboarding_error
    account.last_validation_at = _now()
    account.app_subscribed = validation.app_subscribed
    account.is_active = validation.status == OnboardingStatus.ACTIVE


def build_health_payload(account: WhatsAppAccount, validation=None) -> Dict[str, Any]:
    """Build GET /whatsapp/health response for an account."""
    if validation is None:
        token = account.get_access_token()
        if not token:
            return {
                "portfolio_visible": False,
                "waba_visible": False,
                "phone_visible": False,
                "app_subscribed": False,
                "token_valid": False,
                "permissions_valid": False,
                "status": account.onboarding_status or OnboardingStatus.FAILED,
                "onboarding_error": account.onboarding_error or "No access token",
                "user_message": user_message_for_status(
                    account.onboarding_status or OnboardingStatus.FAILED,
                    account.onboarding_error,
                ),
            }
        validation = revalidate_account(account, persist=False)

    return {
        "portfolio_visible": validation.portfolio_visible,
        "waba_visible": validation.waba_visible,
        "phone_visible": validation.phone_visible,
        "app_subscribed": validation.app_subscribed,
        "token_valid": validation.token_valid,
        "permissions_valid": validation.permissions_valid,
        "status": validation.status,
        "onboarding_error": validation.onboarding_error,
        "user_message": user_message_for_status(validation.status, validation.onboarding_error),
        "checks": validation.checks,
        "account_id": account.id,
        "workspace_id": account.workspace_id,
    }


def revalidate_account(account: WhatsAppAccount, *, persist: bool = True):
    """Re-run discovery + validation for an existing account."""
    from .meta_asset_discovery import ValidationResult

    token = account.get_access_token()
    if not token:
        validation = ValidationResult(
            ok=False,
            status=OnboardingStatus.RECONNECT_REQUIRED,
            onboarding_error="Access token missing",
            checks={"token_valid": False},
        )
        if persist:
            apply_validation_to_account(account, validation)
            db.session.commit()
        return validation

    discovery = discover_whatsapp_assets(token)
    binding = BindingResult(
        waba_id=account.waba_id,
        phone_number_id=account.phone_number_id,
        display_phone_number=account.display_phone_number,
        verified_name=account.verified_name,
        meta_business_id=account.meta_business_id,
    )

    if not binding.waba_id or not binding.phone_number_id:
        resolved = resolve_binding_for_auto_connect(discovery)
        binding.waba_id = binding.waba_id or resolved.waba_id
        binding.phone_number_id = binding.phone_number_id or resolved.phone_number_id
        binding.display_phone_number = binding.display_phone_number or resolved.display_phone_number
        binding.verified_name = binding.verified_name or resolved.verified_name
        binding.meta_business_id = binding.meta_business_id or resolved.meta_business_id

    subscribed = verify_waba_app_subscription(account.waba_id, token) if account.waba_id else False
    if not subscribed and account.waba_id:
        ok, msg, _ = subscribe_waba_to_app(account.waba_id, token)
        if ok:
            subscribed = True
        else:
            logger.warning("Revalidation subscribe failed for WABA %s: %s", account.waba_id, msg)

    validation = validate_discovered_assets(token, discovery, binding, app_subscribed=subscribed)

    if persist:
        if binding.display_phone_number:
            account.display_phone_number = binding.display_phone_number
        if binding.verified_name:
            account.verified_name = binding.verified_name
        if binding.meta_business_id:
            account.meta_business_id = binding.meta_business_id
        apply_validation_to_account(account, validation)
        db.session.commit()

    return validation


def finalize_whatsapp_connection(
    *,
    workspace_id: str,
    user_id: Optional[str],
    access_token: str,
    token_expires_at=None,
    session_waba_id: Optional[str] = None,
    session_phone_id: Optional[str] = None,
    token_type: str = "permanent",
    is_coexistence: bool = False,
    meta_business_id: Optional[str] = None,
    mps_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Complete onboarding after OAuth/token exchange.

    Does NOT mark ACTIVE until all validations pass (including app subscription).
    """
    discovery = discover_whatsapp_assets(access_token)
    binding = resolve_binding_for_auto_connect(discovery, session_waba_id, session_phone_id)

    if binding.phone_number_id:
        conflict = check_phone_available(binding.phone_number_id, workspace_id)
        if conflict:
            return {
                "success": False,
                "error": conflict["error"],
                "error_code": conflict.get("error_code"),
            }

    # Subscribe before final validation (Phase 4 — subscription is required, not best-effort)
    subscribed = False
    subscribe_error = None
    if binding.waba_id:
        subscribed = verify_waba_app_subscription(binding.waba_id, access_token)
        if not subscribed:
            ok, msg, _ = subscribe_waba_to_app(binding.waba_id, access_token)
            subscribed = ok
            if not ok:
                subscribe_error = msg
            else:
                subscribed = verify_waba_app_subscription(binding.waba_id, access_token)

    validation = validate_discovered_assets(
        access_token, discovery, binding, app_subscribed=subscribed
    )
    if subscribe_error and validation.status == OnboardingStatus.SUBSCRIPTION_PENDING:
        validation.onboarding_error = subscribe_error

    account = None
    if binding.phone_number_id:
        account = WhatsAppAccount.query.filter_by(phone_number_id=binding.phone_number_id).first()

    if account:
        account.workspace_id = workspace_id
        account.waba_id = binding.waba_id or account.waba_id
        account.phone_number_id = binding.phone_number_id
        account.display_phone_number = binding.display_phone_number or account.display_phone_number
        account.verified_name = binding.verified_name or account.verified_name
        account.meta_business_id = binding.meta_business_id or account.meta_business_id
        account.connected_by_user_id = user_id or account.connected_by_user_id
        account.set_access_token(access_token, token_type, token_expires_at)
        account.last_synced_at = _now()
        if is_coexistence:
            account.is_coexistence = True
            account.mps_limit = mps_limit or 5
            account.sync_status = account.sync_status or "idle"
            account.coexistence_paired_at = _now()
        if meta_business_id:
            account.meta_business_id = meta_business_id
    elif binding.waba_id and binding.phone_number_id:
        account = WhatsAppAccount(
            workspace_id=workspace_id,
            waba_id=binding.waba_id,
            phone_number_id=binding.phone_number_id,
            display_phone_number=binding.display_phone_number,
            verified_name=binding.verified_name,
            meta_business_id=meta_business_id or binding.meta_business_id,
            connected_by_user_id=user_id,
            is_coexistence=is_coexistence,
            mps_limit=(mps_limit or 5) if is_coexistence else 80,
            sync_status="idle",
        )
        if is_coexistence:
            account.coexistence_paired_at = _now()
        account.set_access_token(access_token, token_type, token_expires_at)
        account.last_synced_at = _now()
        db.session.add(account)
    else:
        status = validation.status
        return {
            "success": False,
            "onboarding_status": status,
            "onboarding_error": validation.onboarding_error,
            "user_message": user_message_for_status(status, validation.onboarding_error),
            "health": {
                "portfolio_visible": validation.portfolio_visible,
                "waba_visible": validation.waba_visible,
                "phone_visible": validation.phone_visible,
                "app_subscribed": validation.app_subscribed,
                "token_valid": validation.token_valid,
                "status": status,
            },
        }

    apply_validation_to_account(account, validation)
    db.session.commit()

    if validation.ok:
        try:
            from .connection_path import _run_post_connection_setup
            _run_post_connection_setup(account.id, account.waba_id, access_token)
        except Exception as exc:
            logger.warning("Post-connection setup after ACTIVE: %s", exc)

    user_message = user_message_for_status(validation.status, validation.onboarding_error)
    payload: Dict[str, Any] = {
        "success": validation.ok,
        "onboarding_status": validation.status,
        "onboarding_error": validation.onboarding_error,
        "user_message": user_message,
        "health": build_health_payload(account, validation),
        "account": account.to_dict(),
    }
    if not validation.ok:
        payload["error"] = validation.onboarding_error or user_message
    return payload
