"""
WhatsApp Utils - Phase 1
========================

Utility functions for WhatsApp module.
"""

import os
import hmac
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================
# Phone Number Utilities
# ============================================================

def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number to canonical E.164 format (digits only, with country code).

    Strategy (industry standard):
    1. Strip all non-digit characters (+, spaces, dashes, parens).
    2. If the result is a 10-digit Indian mobile number (starts with 6-9),
       prepend the country code '91'.
    3. If it has a leading '0' (local dialling), remove it and treat as above.
    4. Otherwise return digits only (already has country code).

    This ensures "9999320932", "919999320932", "+91-9999320932", and
    "09999320932" are ALL stored as "919999320932", preventing duplicate
    conversations for the same person.

    Examples:
        normalize_phone("+91 99993 20932")  -> "919999320932"
        normalize_phone("9999320932")       -> "919999320932"
        normalize_phone("919999320932")     -> "919999320932"
        normalize_phone("+1 650 555 1234")  -> "16505551234"
    """
    if not phone:
        return ""
    # Step 1: strip everything that isn't a digit
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return ""
    # Step 2: remove leading zeros (common in local dialling)
    digits = digits.lstrip("0") or "0"
    # Step 3: Indian mobile numbers — 10 digits starting with 6/7/8/9
    if len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    return digits


def format_phone_display(phone: str) -> str:
    """
    Format phone number for display.
    
    Args:
        phone: Normalized phone number
        
    Returns:
        Formatted phone number with country code prefix
    """
    if not phone:
        return ""
    
    # Add + prefix if not present
    if not phone.startswith("+"):
        phone = f"+{phone}"
    
    return phone


def is_valid_phone(phone: str) -> bool:
    """
    Check if phone number is valid (basic validation).
    
    Args:
        phone: Phone number to validate
        
    Returns:
        True if valid
    """
    normalized = normalize_phone(phone)
    return len(normalized) >= 10 and len(normalized) <= 15 and normalized.isdigit()


# ============================================================
# Signature Verification
# ============================================================

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify webhook signature from Meta.
    
    Args:
        payload: Raw request body
        signature: X-Hub-Signature-256 header value
        secret: App secret from environment
        
    Returns:
        True if signature is valid
    """
    if not signature or not secret:
        return False
    
    # Signature format: sha256=xxxxx
    if not signature.startswith("sha256="):
        return False
    
    expected_signature = signature[7:]  # Remove 'sha256=' prefix
    
    # Calculate HMAC
    computed_signature = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    # Use constant-time comparison
    return hmac.compare_digest(expected_signature, computed_signature)


def generate_verify_token() -> str:
    """
    Generate a random verify token for webhook verification.
    
    Returns:
        Random token string
    """
    import secrets
    return secrets.token_urlsafe(32)


# ============================================================
# Environment Helpers
# ============================================================

def get_whatsapp_config() -> Dict[str, Any]:
    """
    Get WhatsApp configuration from environment variables.
    
    Returns:
        Config dict with all WhatsApp settings
    """
    return {
        "access_token": os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("WHATSAPP_TEMP_TOKEN", ""),
        "phone_number_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        "waba_id": os.getenv("WHATSAPP_WABA_ID", ""),
        "verify_token": os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
        "app_secret": os.getenv("WHATSAPP_APP_SECRET", ""),
        "api_version": os.getenv("WHATSAPP_API_VERSION", "v22.0"),
    }


def is_whatsapp_configured() -> bool:
    """
    Check if WhatsApp is properly configured.
    
    Returns:
        True if required config is present
    """
    config = get_whatsapp_config()
    return bool(config["access_token"] and config["phone_number_id"])


# ============================================================
# Message Parsing
# ============================================================

def extract_message_text(message: Dict[str, Any]) -> Optional[str]:
    """
    Extract text content from a webhook message object.
    
    Args:
        message: Message object from webhook
        
    Returns:
        Text content or None
    """
    msg_type = message.get("type", "")
    
    if msg_type == "text":
        return message.get("text", {}).get("body")
    
    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        int_type = interactive.get("type", "")
        
        if int_type == "button_reply":
            return interactive.get("button_reply", {}).get("title")
        elif int_type == "list_reply":
            return interactive.get("list_reply", {}).get("title")
    
    elif msg_type == "button":
        return message.get("button", {}).get("text")
    
    return None


def get_message_type(message: Dict[str, Any]) -> str:
    """
    Get the type of a message from webhook payload.
    
    Args:
        message: Message object from webhook
        
    Returns:
        Message type string
    """
    return message.get("type", "unknown")


def extract_media_info(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract media information from a message.
    
    Args:
        message: Message object from webhook
        
    Returns:
        Media info dict or None
    """
    msg_type = message.get("type", "")
    
    if msg_type in ("image", "video", "audio", "document", "sticker"):
        media_obj = message.get(msg_type, {})
        return {
            "type": msg_type,
            "id": media_obj.get("id"),
            "mime_type": media_obj.get("mime_type"),
            "sha256": media_obj.get("sha256"),
            "caption": media_obj.get("caption"),
            "filename": media_obj.get("filename"),
        }
    
    return None


# ============================================================
# Timestamp Utilities
# ============================================================

def parse_whatsapp_timestamp(timestamp: str) -> Optional[datetime]:
    """
    Parse a WhatsApp timestamp (Unix epoch seconds).
    
    Args:
        timestamp: Unix timestamp string
        
    Returns:
        datetime object or None
    """
    try:
        ts = int(timestamp)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def format_timestamp(dt: datetime) -> str:
    """
    Format datetime for API response.
    
    Args:
        dt: datetime object
        
    Returns:
        ISO format string
    """
    if not dt:
        return ""
    return dt.isoformat()


def utcnow() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_tz_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure a datetime object is timezone-aware (UTC).
    If naive, replaces tzinfo with UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ============================================================
# Response Helpers
# ============================================================

def success_response(data: Dict[str, Any] = None, message: str = None) -> Dict[str, Any]:
    """
    Create a success response dict.
    
    Args:
        data: Optional data to include
        message: Optional message
        
    Returns:
        Response dict
    """
    response = {"success": True}
    if data:
        response.update(data)
    if message:
        response["message"] = message
    return response


def error_response(error: str, code: str = None, field: str = None) -> Dict[str, Any]:
    """
    Create an error response dict.
    
    Args:
        error: Error message
        code: Optional error code
        field: Optional field name
        
    Returns:
        Response dict
    """
    response = {
        "success": False,
        "error": error,
    }
    if code:
        response["code"] = code
    if field:
        response["field"] = field
    return response


# ============================================================
# Logging Helpers
# ============================================================

def log_webhook_event(event_type: str, phone_number_id: str, details: Dict[str, Any] = None):
    """
    Log a webhook event for debugging.
    
    Args:
        event_type: Type of event (message, status, etc.)
        phone_number_id: Phone number ID
        details: Additional details to log
    """
    logger.info(
        f"WhatsApp webhook event: {event_type} | phone_id: {phone_number_id} | details: {details}"
    )


def log_api_call(endpoint: str, method: str, status: int, response_time_ms: float):
    """
    Log an API call for monitoring.
    
    Args:
        endpoint: API endpoint called
        method: HTTP method
        status: Response status code
        response_time_ms: Response time in milliseconds
    """
    logger.info(
        f"WhatsApp API call: {method} {endpoint} | status: {status} | time: {response_time_ms:.2f}ms"
    )


# ============================================================
# Deduplication
# ============================================================

_processed_wamids = set()
_max_cache_size = 10000


def is_duplicate_message(wamid: str) -> bool:
    """
    Check if message has already been processed (in-memory cache).
    
    Note: This is a simple in-memory cache. For production,
    use Redis or database-backed deduplication.
    
    Args:
        wamid: WhatsApp message ID
        
    Returns:
        True if duplicate
    """
    global _processed_wamids
    
    if wamid in _processed_wamids:
        return True
    
    # Add to cache
    _processed_wamids.add(wamid)
    
    # Clear cache if too large
    if len(_processed_wamids) > _max_cache_size:
        # Keep only the most recent half
        _processed_wamids = set(list(_processed_wamids)[_max_cache_size // 2:])
    
    return False


def clear_dedup_cache():
    """Clear the deduplication cache (for testing)."""
    global _processed_wamids
    _processed_wamids = set()
