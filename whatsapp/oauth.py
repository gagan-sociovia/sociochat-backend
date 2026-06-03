"""
Meta OAuth Integration for WhatsApp Business Account
====================================================
Phase-2 Part-1: Embedded Signup Flow

Handles Meta OAuth flow to connect WhatsApp Business Accounts.
"""

import os
import logging
import secrets
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from flask import session, request

from models import db
from .models import WhatsAppAccount
from .encryption import encrypt_token

logger = logging.getLogger(__name__)

# Meta OAuth Configuration
META_APP_ID = os.getenv("META_APP_ID") or os.getenv("FB_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET") or os.getenv("FB_APP_SECRET")
META_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v22.0")
# OAuth dialog uses a fixed version, but token exchange uses configurable version
META_OAUTH_BASE = f"https://www.facebook.com/{META_API_VERSION}/dialog/oauth"
META_TOKEN_EXCHANGE = f"https://graph.facebook.com/{META_API_VERSION}/oauth/access_token"
META_GRAPH_API = f"https://graph.facebook.com/{META_API_VERSION}"

# Required scopes for WhatsApp Business
REQUIRED_SCOPES = [
    "whatsapp_business_messaging",
    "whatsapp_business_management",
    "business_management",
    "whatsapp_business_manage_events",
]


def get_redirect_uri() -> str:
    """Get OAuth callback URL."""
    app_base = os.getenv("APP_BASE_URL", "https://sociovia-backend-362038465411.europe-west1.run.app")
    return f"{app_base}/api/whatsapp/connect/callback"


def generate_state() -> str:
    """Generate a random state token for OAuth security."""
    return secrets.token_urlsafe(32)


def get_oauth_url(workspace_id: str, user_id: str) -> Dict[str, Any]:
    """
    Generate Meta OAuth URL for Embedded Signup.
    
    Args:
        workspace_id: Workspace ID to associate account with
        user_id: User ID who is connecting the account
        
    Returns:
        Dict with auth_url and state
    """
    if not META_APP_ID:
        raise ValueError("META_APP_ID environment variable not set")
    
    state = generate_state()
    
    # Store state in session for verification
    session[f"wa_oauth_state_{workspace_id}"] = state
    session[f"wa_oauth_workspace_{state}"] = workspace_id
    session[f"wa_oauth_user_{state}"] = user_id
    
    params = {
        "client_id": META_APP_ID,
        "redirect_uri": get_redirect_uri(),
        "state": state,
        "scope": ",".join(REQUIRED_SCOPES),
        "response_type": "code",
    }
    
    auth_url = f"{META_OAUTH_BASE}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    
    return {
        "auth_url": auth_url,
        "state": state,
    }


def exchange_code_for_token(code: str, state: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access token.
    
    Args:
        code: Authorization code from Meta
        state: State token for verification
        
    Returns:
        Dict with access_token, token_type, expires_in
    """
    if not META_APP_SECRET:
        raise ValueError("META_APP_SECRET environment variable not set")
    
    # Verify state
    workspace_id = session.get(f"wa_oauth_workspace_{state}")
    user_id = session.get(f"wa_oauth_user_{state}")
    
    if not workspace_id or not user_id:
        raise ValueError("Invalid or expired OAuth state")
    
    # Exchange code for token
    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": get_redirect_uri(),
        "code": code,
    }
    
    response = requests.get(META_TOKEN_EXCHANGE, params=params, timeout=30)
    response.raise_for_status()
    
    token_data = response.json()
    
    if "error" in token_data:
        raise ValueError(f"Token exchange failed: {token_data['error']}")
    
    access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in")  # Seconds
    
    if not access_token:
        raise ValueError("No access token in response")
    
    # Calculate expiration
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    
    return {
        "access_token": access_token,
        "token_type": token_data.get("token_type", "bearer"),
        "expires_in": expires_in,
        "expires_at": expires_at,
        "workspace_id": workspace_id,
        "user_id": user_id,
    }


def fetch_waba_info(access_token: str) -> Dict[str, Any]:
    """
    Fetch WhatsApp Business Account information from Meta.
    
    Args:
        access_token: Meta access token
        
    Returns:
        Dict with waba_id, phone_numbers, business_name
    """
    # Get WABAs for this token
    url = f"{META_GRAPH_API}/me/businesses"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    
    businesses = response.json().get("data", [])
    
    if not businesses:
        raise ValueError("No WhatsApp Business Accounts found")
    
    # Get first WABA (or allow selection later)
    business_id = businesses[0]["id"]
    
    # Get WABA details
    waba_url = f"{META_GRAPH_API}/{business_id}"
    waba_response = requests.get(waba_url, headers=headers, params={"fields": "id,name"}, timeout=30)
    waba_response.raise_for_status()
    waba_data = waba_response.json()
    
    # Get phone numbers
    phone_url = f"{META_GRAPH_API}/{business_id}/owned_phone_numbers"
    phone_response = requests.get(phone_url, headers=headers, timeout=30)
    phone_response.raise_for_status()
    phone_numbers = phone_response.json().get("data", [])
    
    if not phone_numbers:
        raise ValueError("No phone numbers found in WABA")
    
    # Use first phone number
    phone_data = phone_numbers[0]
    
    return {
        "waba_id": business_id,
        "waba_name": waba_data.get("name", "Unknown"),
        "phone_number_id": phone_data.get("id"),
        "display_phone_number": phone_data.get("display_phone_number"),
        "verified_name": phone_data.get("verified_name"),
    }


def save_whatsapp_account(
    workspace_id: str,
    user_id: str,
    access_token: str,
    token_expires_at: Optional[datetime],
    waba_info: Dict[str, Any],
) -> WhatsAppAccount:
    """
    Save or update WhatsApp account after OAuth — gated by Tech Provider validation.
    ACTIVE is set only when discovery, permissions, and app subscription all pass.
    """
    from .onboarding_service import finalize_whatsapp_connection

    result = finalize_whatsapp_connection(
        workspace_id=workspace_id,
        user_id=user_id,
        access_token=access_token,
        token_expires_at=token_expires_at,
        session_waba_id=waba_info.get("waba_id"),
        session_phone_id=waba_info.get("phone_number_id"),
    )

    if result.get("error_code"):
        raise ValueError(result["error"])

    account_data = result.get("account")
    if not account_data:
        raise ValueError(result.get("error") or result.get("user_message") or "Onboarding validation failed")

    account = WhatsAppAccount.query.get(account_data["id"])
    if not account:
        raise ValueError("Account save failed")

    if not result.get("success"):
        logger.warning(
            "WhatsApp account saved but not ACTIVE: status=%s error=%s",
            result.get("onboarding_status"),
            result.get("onboarding_error"),
        )

    return account


def exchange_short_for_long_token(short_token: str) -> Dict[str, Any]:
    """
    Exchange a short-lived access token from Facebook SDK for a long-lived token.
    """
    if not META_APP_ID:
        raise ValueError("META_APP_ID environment variable not set")
    if not META_APP_SECRET:
        raise ValueError("META_APP_SECRET environment variable not set")

    exchange_url = f"{META_GRAPH_API}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": short_token,
    }

    response = requests.get(exchange_url, params=params, timeout=30)

    if response.status_code != 200:
        error_data = response.json() if response.content else {}
        error_msg = error_data.get("error", {}).get("message", "Token exchange failed")
        logger.error("Facebook token exchange failed: %s", error_msg)
        raise ValueError(error_msg)

    token_data = response.json()

    if "error" in token_data:
        raise ValueError(f"Token exchange failed: {token_data['error']}")

    long_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in")

    if not long_token:
        raise ValueError("No access token in exchange response")

    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    return {
        "access_token": long_token,
        "token_type": token_data.get("token_type", "bearer"),
        "expires_in": expires_in,
        "expires_at": expires_at,
    }


def _exchange_code(code: str, *, redirect_uri: Optional[str] = None) -> str:
    """Exchange OAuth code for short-lived token."""
    if not META_APP_ID or not META_APP_SECRET:
        raise ValueError("META_APP_ID and META_APP_SECRET must be set")

    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "code": code,
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    response = requests.get(META_TOKEN_EXCHANGE, params=params, timeout=30)
    token_data = response.json()
    if response.status_code >= 400 or "error" in token_data:
        message = token_data.get("error", {}).get("message", "Token exchange failed")
        raise ValueError(message)

    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("No access_token in response")
    return access_token


def exchange_embedded_signup_code(code: str) -> str:
    """Exchange Embedded Signup auth code for a long-lived user access token."""
    short_token = _exchange_code(code)
    return exchange_short_for_long_token(short_token)["access_token"]


def exchange_oauth_callback_code(code: str, redirect_uri: Optional[str] = None) -> str:
    """Exchange popup OAuth callback code for a long-lived user access token."""
    uri = redirect_uri or get_redirect_uri()
    short_token = _exchange_code(code, redirect_uri=uri)
    return exchange_short_for_long_token(short_token)["access_token"]


def validate_facebook_token(access_token: str) -> Dict[str, Any]:
    """
    Validate a Facebook access token and return user/app info.
    
    Args:
        access_token: Facebook access token to validate
        
    Returns:
        Dict with user_id, app_id, is_valid, scopes
    """
    debug_url = f"{META_GRAPH_API}/debug_token"
    params = {
        "input_token": access_token,
        "access_token": f"{META_APP_ID}|{META_APP_SECRET}",
    }
    
    response = requests.get(debug_url, params=params, timeout=30)
    
    if response.status_code != 200:
        return {"is_valid": False, "error": "Failed to validate token"}
    
    data = response.json().get("data", {})
    
    return {
        "is_valid": data.get("is_valid", False),
        "user_id": data.get("user_id"),
        "app_id": data.get("app_id"),
        "scopes": data.get("scopes", []),
        "expires_at": data.get("expires_at"),
    }

