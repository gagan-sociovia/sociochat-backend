# WhatsApp Module - Phase 1

## Overview

This module provides WhatsApp Cloud API integration for Sociovia. It handles:
- Sending messages (text, template, media, interactive)
- Receiving webhooks (incoming messages, delivery status)
- Template message handling with proper payload generation
- Conversation management

## Important: Template Messages in Test Mode

### Why Templates Are Mandatory in Test Mode

Meta's WhatsApp Business API has strict rules in test mode:

1. **Only APPROVED templates can deliver messages**
   - Even if the API returns a `wamid` (WhatsApp Message ID), the message will NOT be delivered unless the template is approved
   - The `hello_world` template is pre-approved by Meta for testing

2. **Free-form messages (text) only work after user initiates conversation**
   - In test mode, you cannot send free-form text to a user unless they messaged you first within 24 hours
   - Templates bypass this restriction

3. **wamid exists but message not delivered?**
   - This is expected behavior in test mode
   - The API accepts the request (returns wamid) but Meta's delivery system rejects it
   - Check the webhook for `failed` status updates

### Template Payload Rules

Meta's Cloud API is VERY strict about template payloads:

```
┌─────────────────────────────────────────────────────────────┐
│ CRITICAL: Templates without variables must NOT include      │
│ the "components" field AT ALL - not even an empty array!   │
└─────────────────────────────────────────────────────────────┘
```

#### ✅ Correct: hello_world (no variables)
```json
{
  "messaging_product": "whatsapp",
  "to": "919876543210",
  "type": "template",
  "template": {
    "name": "hello_world",
    "language": { "code": "en_US" }
  }
}
```

#### ❌ WRONG: hello_world with empty components
```json
{
  "messaging_product": "whatsapp",
  "to": "919876543210",
  "type": "template",
  "template": {
    "name": "hello_world",
    "language": { "code": "en_US" },
    "components": []
  }
}
```

#### ✅ Correct: Template with body variables
```json
{
  "messaging_product": "whatsapp",
  "to": "919876543210",
  "type": "template",
  "template": {
    "name": "order_update",
    "language": { "code": "en" },
    "components": [
      {
        "type": "body",
        "parameters": [
          { "type": "text", "text": "John" },
          { "type": "text", "text": "12345" }
        ]
      }
    ]
  }
}
```

#### ✅ Correct: Template with image header
```json
{
  "messaging_product": "whatsapp",
  "to": "919876543210",
  "type": "template",
  "template": {
    "name": "promo_with_image",
    "language": { "code": "en" },
    "components": [
      {
        "type": "header",
        "parameters": [
          {
            "type": "image",
            "image": { "link": "https://example.com/image.jpg" }
          }
        ]
      },
      {
        "type": "body",
        "parameters": [
          { "type": "text", "text": "Summer Sale" }
        ]
      }
    ]
  }
}
```

## API Endpoints

### Send Template Message

```
POST /api/whatsapp/send/template
```

**Simple template (hello_world):**
```json
{
  "to": "919876543210",
  "template_name": "hello_world",
  "language": "en_US"
}
```

**Template with body parameters:**
```json
{
  "to": "919876543210",
  "template_name": "order_update",
  "language": "en",
  "params": ["John", "12345", "$99.00"]
}
```

**Template with image header:**
```json
{
  "to": "919876543210",
  "template_name": "promo_image",
  "language": "en",
  "header_image_url": "https://example.com/image.jpg",
  "params": ["Summer Sale"]
}
```

### Using TemplateBuilder (Recommended)

```python
from whatsapp.template_builder import TemplateBuilder

# Simple template - NO components
builder = TemplateBuilder("hello_world", "en_US")
payload = builder.build_with_recipient("919876543210")
# Result: {"messaging_product": "whatsapp", "to": "919876543210", "type": "template", "template": {"name": "hello_world", "language": {"code": "en_US"}}}

# Template with body params
builder = TemplateBuilder("order_update", "en")
builder.add_body_params(["John", "12345"])
payload = builder.build_with_recipient("919876543210")

# Template with image header
builder = TemplateBuilder("promo_image", "en")
builder.add_header_image("https://example.com/image.jpg")
builder.add_body_params(["Summer Sale"])
payload = builder.build_with_recipient("919876543210")
```

## Troubleshooting

### Message shows "sent" but never delivered

1. Check if template is approved in Meta Business Suite
2. Check if language code matches exactly (e.g., `en_US` vs `en`)
3. Check webhook logs for status updates - look for `failed` events
4. In test mode, only `hello_world` and your approved templates work

### API returns error about template not found

1. Template name is case-sensitive and must be lowercase with underscores
2. Template must be approved (not just submitted)
3. Check the exact language code used during template creation

### Components error

1. DO NOT include `components` for templates without variables
2. Check that component types match template structure (header, body, button)
3. Parameters must have correct types (text, image, video, document)

## File Structure

```
whatsapp/
├── __init__.py          # Module exports
├── models.py            # Database models (Account, Conversation, Message, Template)
├── services.py          # WhatsApp Cloud API service
├── routes.py            # Flask Blueprint with API endpoints
├── validators.py        # Request validation
├── utils.py             # Utility functions
├── webhook.py           # Webhook processing
├── template_builder.py  # Template payload generator
└── README.md            # This file
```

## Database Models

### WhatsAppTemplate

Stores metadata about approved templates for proper payload generation:

| Column | Type | Description |
|--------|------|-------------|
| name | String | Template name (lowercase, underscores) |
| language | String | Language code (en, en_US, etc.) |
| has_header | Boolean | Whether template has a header |
| header_type | String | none, text, image, video, document |
| body_variable_count | Integer | Number of {{1}}, {{2}} in body |
| has_buttons | Boolean | Whether template has buttons |
| status | String | PENDING, APPROVED, REJECTED |

## Best Practices

1. **Always use TemplateBuilder** for generating payloads
2. **Never hardcode template logic** - use the `WhatsAppTemplate` model
3. **Log the exact payload** sent for debugging
4. **Check webhook logs** when messages don't deliver
5. **Test with hello_world first** before custom templates
