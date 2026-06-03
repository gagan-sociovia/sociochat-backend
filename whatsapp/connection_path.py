"""
WhatsApp Connection Path Detection
===================================
BSP-level implementation for dual-path WhatsApp connection.

Detects whether a workspace should use:
- Path A: Embedded Signup (NEW numbers only)
- Path B: Manual Linking (existing WABA + token)

CRITICAL META RULES:
1. Embedded Signup = ONLY for brand-new WhatsApp numbers
2. Existing WhatsApp Cloud API numbers CANNOT be reused via Embedded Signup
3. A number can be connected to ONLY one integration at a time
4. Test numbers (like 15558016716) behave differently
"""

import os
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from models import db
from .models import WhatsAppAccount
from .encryption import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)

META_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
META_GRAPH_API = f"https://graph.facebook.com/{META_API_VERSION}"


# ============================================================
# Connection Status Constants
# ============================================================

class ConnectionStatus:
    """
    WhatsApp connection status states.
    
    NO_ACCOUNT: No WhatsApp account exists for this workspace
    CONNECTED: Fully connected with valid token + phone_number_id
    PARTIAL: Has WABA but missing phone_number_id or incomplete setup
    RELINK_REQUIRED: Token expired or invalid, needs re-authentication
    """
    NO_ACCOUNT = "NO_ACCOUNT"
    CONNECTED = "CONNECTED"
    PARTIAL = "PARTIAL"
    RELINK_REQUIRED = "RELINK_REQUIRED"


class RecommendedPath:
    """Recommended connection path for the user."""
    EMBEDDED = "EMBEDDED"  # Use Embedded Signup (new number)
    MANUAL = "MANUAL"      # Use manual linking (existing account)


# ============================================================
# Token Validation
# ============================================================

def validate_token_with_meta(access_token: str) -> Dict[str, Any]:
    """
    Validate access token by calling Meta Graph API.
    
    Args:
        access_token: The token to validate
        
    Returns:
        Dict with:
        - valid: bool
        - user_id: str | None
        - error: str | None
        - permissions: list | None
    """
    if not access_token:
        return {"valid": False, "error": "No token provided"}
    
    try:
        # Call /me to validate token
        url = f"{META_GRAPH_API}/me"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"fields": "id,name"}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return {
                "valid": True,
                "user_id": data.get("id"),
                "name": data.get("name"),
                "error": None
            }
        elif response.status_code == 401:
            return {"valid": False, "error": "Token expired or revoked"}
        else:
            error_data = response.json().get("error", {})
            return {
                "valid": False,
                "error": error_data.get("message", f"HTTP {response.status_code}")
            }
    except requests.exceptions.Timeout:
        return {"valid": False, "error": "Meta API timeout"}
    except requests.exceptions.RequestException as e:
        logger.exception(f"Token validation request failed: {e}")
        return {"valid": False, "error": str(e)}


def check_phone_number_status(access_token: str, phone_number_id: str) -> Dict[str, Any]:
    """
    Check phone number status from Meta API.
    
    Returns:
        Dict with display_name status, quality rating, etc.
    """
    if not access_token or not phone_number_id:
        return {"valid": False, "error": "Missing token or phone_number_id"}
    
    try:
        url = f"{META_GRAPH_API}/{phone_number_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"fields": "display_phone_number,verified_name,code_verification_status,quality_rating,name_status"}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Detect if this is a test number (Meta formats like +1 555-XXX-XXXX)
            display_number = data.get("display_phone_number", "")
            is_test_number = display_number.startswith("+1 555") or "15558" in display_number.replace(" ", "").replace("-", "")
            
            return {
                "valid": True,
                "display_phone_number": display_number,
                "verified_name": data.get("verified_name"),
                "name_status": data.get("name_status"),  # APPROVED, PENDING, DECLINED
                "quality_rating": data.get("quality_rating"),  # GREEN, YELLOW, RED
                "is_test_number": is_test_number
            }
        else:
            error_data = response.json().get("error", {})
            return {
                "valid": False,
                "error": error_data.get("message", f"HTTP {response.status_code}")
            }
    except requests.exceptions.RequestException as e:
        logger.exception(f"Phone number status check failed: {e}")
        return {"valid": False, "error": str(e)}


# ============================================================
# Main Detection Function
# ============================================================

def detect_whatsapp_connection_path(workspace_id: str) -> Dict[str, Any]:
    """
    Determine which connection path a workspace should use.
    
    This is the core detection function that tells the frontend
    whether to show Embedded Signup or Manual Linking.
    
    Args:
        workspace_id: The workspace to check
        
    Returns:
        {
            "status": "NO_ACCOUNT" | "CONNECTED" | "PARTIAL" | "RELINK_REQUIRED",
            "recommended_path": "EMBEDDED" | "MANUAL" | None,
            "reason": str,
            "account_summary": {
                "id": int,
                "waba_id": str,
                "phone_number": str | None,
                "phone_number_id": str | None,
                "display_name_status": "APPROVED" | "IN_REVIEW" | None,
                "quality_rating": str | None,
                "is_test_number": bool,
                "is_active": bool
            } | None,
            "can_use_embedded_signup": bool,
            "can_use_manual_link": bool
        }
    """
    if not workspace_id:
        return {
            "status": ConnectionStatus.NO_ACCOUNT,
            "recommended_path": RecommendedPath.EMBEDDED,
            "reason": "No workspace ID provided",
            "account_summary": None,
            "can_use_embedded_signup": True,
            "can_use_manual_link": True,
        }
    
    # Query for existing WhatsApp account (active first, then any)
    account = WhatsAppAccount.query.filter_by(
        workspace_id=workspace_id,
        is_active=True
    ).first()
    
    # ============================================================
    # FAST PATH: Fully onboarded active account
    # ============================================================
    if account and account.phone_number_id and account.get_access_token():
        onboarding_status = getattr(account, "onboarding_status", None) or "ACTIVE"
        if onboarding_status == "ACTIVE" and account.is_active:
            account_summary = {
                "id": account.id,
                "waba_id": account.waba_id,
                "phone_number": account.display_phone_number,
                "phone_number_id": account.phone_number_id,
                "verified_name": account.custom_name or account.verified_name,
                "display_name_status": None,
                "quality_rating": account.quality_score,
                "is_test_number": False,
                "is_active": account.is_active,
                "token_type": account.token_type,
                "onboarding_status": onboarding_status,
                "onboarding_error": getattr(account, "onboarding_error", None),
            }
            return {
                "status": ConnectionStatus.CONNECTED,
                "recommended_path": None,
                "reason": "WhatsApp Business account is fully connected",
                "account_summary": account_summary,
                "can_use_embedded_signup": False,
                "can_use_manual_link": True,
                "onboarding_status": onboarding_status,
            }
    
    # Pending Tech Provider onboarding — surface granular status to frontend
    pending_account = WhatsAppAccount.query.filter_by(workspace_id=workspace_id).order_by(
        WhatsAppAccount.id.desc()
    ).first()
    if pending_account and getattr(pending_account, "onboarding_status", None):
        ob_status = pending_account.onboarding_status
        if ob_status != "ACTIVE":
            from .onboarding_status import user_message_for_status
            account_summary = {
                "id": pending_account.id,
                "waba_id": pending_account.waba_id,
                "phone_number": pending_account.display_phone_number,
                "phone_number_id": pending_account.phone_number_id,
                "verified_name": pending_account.custom_name or pending_account.verified_name,
                "display_name_status": None,
                "quality_rating": pending_account.quality_score,
                "is_test_number": False,
                "is_active": pending_account.is_active,
                "token_type": pending_account.token_type,
                "onboarding_status": ob_status,
                "onboarding_error": pending_account.onboarding_error,
            }
            conn_status = ConnectionStatus.RELINK_REQUIRED if ob_status == "RECONNECT_REQUIRED" else ConnectionStatus.PARTIAL
            return {
                "status": conn_status,
                "recommended_path": RecommendedPath.EMBEDDED if ob_status != "RECONNECT_REQUIRED" else RecommendedPath.MANUAL,
                "reason": user_message_for_status(ob_status, pending_account.onboarding_error),
                "account_summary": account_summary,
                "can_use_embedded_signup": True,
                "can_use_manual_link": True,
                "onboarding_status": ob_status,
            }

    # If no active account, check for inactive ones (user explicitly unlinked)
    if not account:
        account = WhatsAppAccount.query.filter_by(
            workspace_id=workspace_id
        ).order_by(WhatsAppAccount.id.desc()).first()
        
        if account:
            # Found an inactive account — DON'T auto-reactivate.
            # The user explicitly unlinked, so let them choose to reconnect.
            account_summary = {
                "id": account.id,
                "waba_id": account.waba_id,
                "phone_number": account.display_phone_number,
                "phone_number_id": account.phone_number_id,
                "verified_name": account.custom_name or account.verified_name,
                "display_name_status": None,
                "quality_rating": account.quality_score,
                "is_test_number": False,
                "is_active": False,
                "token_type": account.token_type,
            }
            return {
                "status": ConnectionStatus.RELINK_REQUIRED,
                "recommended_path": RecommendedPath.MANUAL,
                "reason": "Account was unlinked — reconnect or switch to a different account",
                "account_summary": account_summary,
                "can_use_embedded_signup": True,   # Allow switching to a new number
                "can_use_manual_link": True,
            }
    
    # ============================================================
    # Case 1: No account exists → Path A (Embedded Signup)
    # ============================================================
    if not account:
        return {
            "status": ConnectionStatus.NO_ACCOUNT,
            "recommended_path": RecommendedPath.EMBEDDED,
            "reason": "No WhatsApp account connected to this workspace",
            "account_summary": None,
            "can_use_embedded_signup": True,
            "can_use_manual_link": True,  # User can still manually link if they have credentials
        }
    
    # ============================================================
    # Case 2: Account exists but incomplete — check completeness
    # ============================================================
    
    # Build account summary
    account_summary = {
        "id": account.id,
        "waba_id": account.waba_id,
        "phone_number": account.display_phone_number,
        "phone_number_id": account.phone_number_id,
        "verified_name": account.custom_name or account.verified_name,
        "display_name_status": None,
        "quality_rating": account.quality_score,
        "is_test_number": False,
        "is_active": account.is_active,
        "token_type": account.token_type,
    }
    
    # Check if phone_number_id is missing → PARTIAL
    if not account.phone_number_id:
        return {
            "status": ConnectionStatus.PARTIAL,
            "recommended_path": RecommendedPath.MANUAL,
            "reason": "WhatsApp account exists but phone number setup is incomplete",
            "account_summary": account_summary,
            "can_use_embedded_signup": False,  # CRITICAL: Hide Embedded Signup if ANY account exists
            "can_use_manual_link": True,
        }
    
    # Check if token exists
    access_token = account.get_access_token()
    
    if not access_token:
        return {
            "status": ConnectionStatus.RELINK_REQUIRED,
            "recommended_path": RecommendedPath.MANUAL,
            "reason": "Access token is missing - please reconnect your account",
            "account_summary": account_summary,
            "can_use_embedded_signup": False,  # CRITICAL: Hide Embedded Signup
            "can_use_manual_link": True,
        }
    
    # For accounts that reached here (e.g. just re-activated), do a quick token check
    token_check = validate_token_with_meta(access_token)
    
    if not token_check.get("valid"):
        # Token expired or invalid
        return {
            "status": ConnectionStatus.RELINK_REQUIRED,
            "recommended_path": RecommendedPath.MANUAL,
            "reason": f"Access token is invalid: {token_check.get('error', 'Unknown error')}",
            "account_summary": account_summary,
            "can_use_embedded_signup": False,  # CRITICAL: Hide Embedded Signup
            "can_use_manual_link": True,
        }
    
    # ============================================================
    # Case 3: Fully connected (re-activated or was missing from fast path)
    # ============================================================
    return {
        "status": ConnectionStatus.CONNECTED,
        "recommended_path": None,  # Already connected, no path needed
        "reason": "WhatsApp Business account is fully connected",
        "account_summary": account_summary,
        "can_use_embedded_signup": False,  # CRITICAL: Never show Embedded Signup for connected accounts
        "can_use_manual_link": True,  # Can still re-link if needed
    }


# ============================================================
# Manual Connection (Path B)
# ============================================================

def connect_manual(
    workspace_id: str,
    user_id: str,
    waba_id: str,
    phone_number_id: str,
    access_token: str,
) -> Dict[str, Any]:
    """
    Connect an existing WhatsApp account via manual credentials.
    
    SAFETY RULES:
    1. Validate token before saving
    2. Never overwrite a working token
    3. Check workspace isolation
    4. Encrypt token at rest
    
    Args:
        workspace_id: Target workspace
        user_id: User performing the connection
        waba_id: WhatsApp Business Account ID
        phone_number_id: Phone Number ID
        access_token: Access token from Meta
        
    Returns:
        Dict with success status and account info
    """
    logger.info(f"Manual WhatsApp connection attempt: workspace={workspace_id}, waba={waba_id}")
    
    # Validate inputs
    if not all([workspace_id, user_id, waba_id, phone_number_id, access_token]):
        return {
            "success": False,
            "error": "Missing required fields",
            "error_code": "MISSING_FIELDS"
        }
    
    # Step 1: Validate token with Meta
    token_check = validate_token_with_meta(access_token)
    if not token_check.get("valid"):
        return {
            "success": False,
            "error": f"Invalid access token: {token_check.get('error', 'Token validation failed')}",
            "error_code": "INVALID_TOKEN"
        }
    
    # Step 2: Validate phone_number_id
    phone_status = check_phone_number_status(access_token, phone_number_id)
    if not phone_status.get("valid"):
        return {
            "success": False,
            "error": f"Invalid phone number ID: {phone_status.get('error', 'Phone number validation failed')}",
            "error_code": "INVALID_PHONE"
        }
    
    # Step 3: Check if this WABA+phone already exists ANYWHERE in the database
    existing_account = WhatsAppAccount.query.filter_by(
        waba_id=waba_id,
        phone_number_id=phone_number_id,
    ).first()
    
    # Step 4: Handle existing account scenarios
    if existing_account:
        # Normalise workspace_id comparison (both as strings)
        existing_ws = str(existing_account.workspace_id) if existing_account.workspace_id else None
        target_ws = str(workspace_id) if workspace_id else None
        
        # Case A: Account belongs to a DIFFERENT workspace and is active
        if existing_ws != target_ws and existing_account.is_active:
            logger.warning(f"WABA {waba_id} + phone {phone_number_id} already connected to workspace {existing_account.workspace_id}")
            return {
                "success": False,
                "error": "This WhatsApp number is already connected to another workspace. Disconnect it there first.",
                "error_code": "ALREADY_CONNECTED_OTHER"
            }
        
        # Case B: Account belongs to a DIFFERENT workspace but is inactive - transfer it
        if existing_ws != target_ws and not existing_account.is_active:
            logger.info(f"Transferring inactive account from workspace {existing_account.workspace_id} to {workspace_id}")
            existing_account.workspace_id = workspace_id
            db.session.commit()

    from .onboarding_service import finalize_whatsapp_connection

    is_temporary = phone_status.get("is_test_number", False) or len(access_token) < 200
    token_type = "temporary" if is_temporary else "permanent"

    result = finalize_whatsapp_connection(
        workspace_id=workspace_id,
        user_id=user_id,
        access_token=access_token,
        session_waba_id=waba_id,
        session_phone_id=phone_number_id,
        token_type=token_type,
    )

    if result.get("success"):
        result.setdefault("message", "Account connected successfully")
    else:
        result.setdefault("error_code", "ONBOARDING_INCOMPLETE")

    return result


def _run_post_connection_setup(account_id: int, waba_id: str, access_token: str) -> Dict[str, Any]:
    """
    Run post-connection setup tasks:
    1. Subscribe WABA to webhooks
    2. Validate webhook configuration
    3. Log any issues for debugging
    
    This ensures new users don't have to manually configure webhooks.
    """
    setup_results = {
        "webhook_subscription": None,
        "health_check": None,
        "issues": []
    }
    
    try:
        from .health_check import subscribe_waba_to_webhooks, perform_health_check
        
        # Auto-subscribe WABA to webhooks
        success, message, details = subscribe_waba_to_webhooks(waba_id, access_token)
        setup_results["webhook_subscription"] = {
            "success": success,
            "message": message,
            "details": details
        }
        
        if not success:
            setup_results["issues"].append(f"Webhook subscription failed: {message}")
            logger.warning(f"⚠️ Webhook subscription failed for account {account_id}: {message}")
        else:
            logger.info(f"✅ Webhook subscription successful for account {account_id}")
        
        # Run health check
        health_result = perform_health_check(account_id, auto_fix=True)
        setup_results["health_check"] = health_result
        
        if health_result.get("overall_status") == "critical":
            setup_results["issues"].append("Health check found critical issues")
        
    except Exception as e:
        logger.exception(f"Post-connection setup error: {e}")
        setup_results["issues"].append(f"Setup error: {str(e)}")
    
    return setup_results
