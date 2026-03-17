"""
WhatsApp Validators - Phase 1
=============================

Request validation helpers for WhatsApp API endpoints.
Provides clean input validation and error messages.
"""

import re
from typing import Dict, Any, List, Optional, Tuple

# Phone number validation regex (E.164 without +)
PHONE_REGEX = re.compile(r"^\d{10,15}$")


class ValidationError(Exception):
    """Custom validation error with field name and message."""
    
    def __init__(self, field: str, message: str, code: str = "validation_error"):
        self.field = field
        self.message = message
        self.code = code
        super().__init__(f"{field}: {message}")
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "field": self.field,
            "message": self.message,
            "code": self.code,
        }


def validate_phone_number(phone: str, field: str = "to") -> str:
    """
    Validate and normalize a phone number.
    
    Args:
        phone: Phone number to validate
        field: Field name for error messages
        
    Returns:
        Normalized phone number
        
    Raises:
        ValidationError: If phone number is invalid
    """
    if not phone:
        raise ValidationError(field, "Phone number is required", "required")
    
    # Normalize: remove +, spaces, dashes
    normalized = phone.replace("+", "").replace(" ", "").replace("-", "").strip()
    
    if not normalized:
        raise ValidationError(field, "Phone number is required", "required")
    
    if not PHONE_REGEX.match(normalized):
        raise ValidationError(
            field,
            "Invalid phone number format. Must be 10-15 digits (E.164 format without +)",
            "invalid_format",
        )
    
    return normalized


def validate_text_message(data: Dict[str, Any]) -> Tuple[str, str]:
    """
    Validate text message request.
    
    Args:
        data: Request data
        
    Returns:
        Tuple of (to, text)
        
    Raises:
        ValidationError: If validation fails
    """
    to = validate_phone_number(data.get("to", ""))
    
    text = data.get("text", "")
    if not text:
        raise ValidationError("text", "Message text is required", "required")
    
    if len(text) > 4096:
        raise ValidationError(
            "text",
            "Message text is too long. Maximum 4096 characters.",
            "max_length",
        )
    
    return to, text


def validate_template_message(data: Dict[str, Any]) -> Tuple[str, str, str, Optional[List]]:
    """
    Validate template message request.
    
    Args:
        data: Request data
        
    Returns:
        Tuple of (to, template_name, language, components)
        
    Raises:
        ValidationError: If validation fails
    """
    to = validate_phone_number(data.get("to", ""))
    
    template_name = data.get("template_name") or data.get("template", "")
    if not template_name:
        raise ValidationError("template_name", "Template name is required", "required")
    
    # Validate template name format
    if not re.match(r"^[a-z0-9_]+$", template_name):
        raise ValidationError(
            "template_name",
            "Template name must contain only lowercase letters, numbers, and underscores",
            "invalid_format",
        )
    
    language = data.get("language", "en")
    
    # Check for components from multiple possible fields
    components = data.get("components") or data.get("params")
    body_params = data.get("body_params")
    
    # If body_params is provided (from frontend), convert to components format
    # OR pass through if it's a dict (named parameters) for TemplateBuilder
    if body_params:
        if isinstance(body_params, list) and len(body_params) > 0:
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in body_params],
            }]
        elif isinstance(body_params, dict):
            # Named parameters - pass as is, service layer will use TemplateBuilder
            # We return it in the components tuple slot, but as a dict wrapper
            components = [{"type": "body", "named_params": body_params}]
            
    # Convert simple params list to components format
    elif isinstance(components, list) and len(components) > 0:
        if not isinstance(components[0], dict):
            # Simple list of values - convert to body parameters
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in components],
            }]
    
    return to, template_name, language, components


def validate_media_message(data: Dict[str, Any]) -> Tuple[str, str, Optional[str], Optional[str], Optional[str]]:
    """
    Validate media message request.
    
    Args:
        data: Request data
        
    Returns:
        Tuple of (to, media_type, url, media_id, caption)
        
    Raises:
        ValidationError: If validation fails
    """
    to = validate_phone_number(data.get("to", ""))
    
    media_type = data.get("media_type", data.get("type", "image"))
    valid_types = ["image", "video", "audio", "document"]
    if media_type not in valid_types:
        raise ValidationError(
            "media_type",
            f"Invalid media type. Must be one of: {', '.join(valid_types)}",
            "invalid_value",
        )
    
    # Get media from nested object or top level
    media = data.get("media", {})
    url = media.get("url") or data.get("media_url") or data.get("url")
    media_id = media.get("id") or data.get("media_id")
    
    if not url and not media_id:
        raise ValidationError(
            "media_url",
            "Either media_url or media_id is required",
            "required",
        )
    
    # Validate URL format if provided
    if url and not url.startswith(("http://", "https://")):
        raise ValidationError(
            "media_url",
            "Invalid URL format. Must start with http:// or https://",
            "invalid_format",
        )
    
    caption = media.get("caption") or data.get("caption")
    
    return to, media_type, url, media_id, caption


def validate_interactive_buttons(data: Dict[str, Any]) -> Tuple[str, str, List[Dict], Optional[str], Optional[str]]:
    """
    Validate interactive buttons message request.
    
    Args:
        data: Request data
        
    Returns:
        Tuple of (to, body_text, buttons, header, footer)
        
    Raises:
        ValidationError: If validation fails
    """
    to = validate_phone_number(data.get("to", ""))
    
    # Get interactive data from nested object or top level
    interactive = data.get("interactive", {})
    body_text = interactive.get("body") or data.get("body", "")
    
    if not body_text:
        raise ValidationError("body", "Body text is required", "required")
    
    if len(body_text) > 1024:
        raise ValidationError("body", "Body text is too long. Maximum 1024 characters.", "max_length")
    
    buttons = interactive.get("buttons") or data.get("buttons", [])
    
    if not buttons:
        raise ValidationError("buttons", "At least one button is required", "required")
    
    if len(buttons) > 3:
        raise ValidationError("buttons", "Maximum 3 buttons allowed", "max_count")
    
    # Validate button format
    validated_buttons = []
    for i, btn in enumerate(buttons):
        if not isinstance(btn, dict):
            raise ValidationError(f"buttons[{i}]", "Button must be an object", "invalid_type")
        
        btn_id = btn.get("id", str(i + 1))
        btn_title = btn.get("title", "")
        
        if not btn_title:
            raise ValidationError(f"buttons[{i}].title", "Button title is required", "required")
        
        if len(btn_title) > 20:
            raise ValidationError(f"buttons[{i}].title", "Button title too long. Maximum 20 characters.", "max_length")
        
        validated_buttons.append({"id": btn_id, "title": btn_title})
    
    header = interactive.get("header") or data.get("header")
    footer = interactive.get("footer") or data.get("footer")
    
    if header and len(header) > 60:
        raise ValidationError("header", "Header too long. Maximum 60 characters.", "max_length")
    
    if footer and len(footer) > 60:
        raise ValidationError("footer", "Footer too long. Maximum 60 characters.", "max_length")
    
    return to, body_text, validated_buttons, header, footer


def validate_interactive_list(data: Dict[str, Any]) -> Tuple[str, str, str, List[Dict], Optional[str], Optional[str]]:
    """
    Validate interactive list message request.
    
    Args:
        data: Request data
        
    Returns:
        Tuple of (to, body_text, button_text, sections, header, footer)
        
    Raises:
        ValidationError: If validation fails
    """
    to = validate_phone_number(data.get("to", ""))
    
    # Get interactive data from nested object or top level
    interactive = data.get("interactive", {})
    body_text = interactive.get("body") or data.get("body", "")
    
    if not body_text:
        raise ValidationError("body", "Body text is required", "required")
    
    button_text = interactive.get("button") or data.get("button_text", "Options")
    
    if len(button_text) > 20:
        raise ValidationError("button_text", "Button text too long. Maximum 20 characters.", "max_length")
    
    sections = interactive.get("sections") or data.get("sections", [])
    
    if not sections:
        raise ValidationError("sections", "At least one section is required", "required")
    
    if len(sections) > 10:
        raise ValidationError("sections", "Maximum 10 sections allowed", "max_count")
    
    # Validate sections
    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValidationError(f"sections[{i}]", "Section must be an object", "invalid_type")
        
        rows = section.get("rows", [])
        if not rows:
            raise ValidationError(f"sections[{i}].rows", "Section must have at least one row", "required")
        
        if len(rows) > 10:
            raise ValidationError(f"sections[{i}].rows", "Maximum 10 rows per section", "max_count")
    
    header = interactive.get("header") or data.get("header")
    footer = interactive.get("footer") or data.get("footer")
    
    return to, body_text, button_text, sections, header, footer


def validate_webhook_payload(payload: Dict[str, Any]) -> bool:
    """
    Validate incoming webhook payload structure.
    
    Args:
        payload: Webhook payload from Meta
        
    Returns:
        True if valid structure
    """
    if not payload:
        return False
    
    # Must have 'object' field
    if payload.get("object") != "whatsapp_business_account":
        return False
    
    # Must have 'entry' array
    entry = payload.get("entry")
    if not isinstance(entry, list) or len(entry) == 0:
        return False
    
    return True


def format_validation_error(error: ValidationError) -> Dict[str, Any]:
    """
    Format validation error for API response.
    
    Args:
        error: ValidationError instance
        
    Returns:
        Formatted error dict
    """
    return {
        "success": False,
        "error": error.message,
        "field": error.field,
        "code": error.code,
    }
