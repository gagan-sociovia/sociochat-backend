# WhatsApp Flows - Complete Documentation

## Table of Contents
1. [What is WhatsApp Flows?](#what-is-whatsapp-flows)
2. [Architecture Overview](#architecture-overview)
3. [Implementation Phases](#implementation-phases)
4. [Getting Started](#getting-started)
5. [Creating Your First Flow](#creating-your-first-flow)
6. [Sending Flows in Chats](#sending-flows-in-chats)
7. [Receiving & Storing Data](#receiving--storing-data)
8. [API Reference](#api-reference)
9. [Security & Access Control](#security--access-control)
10. [UX Best Practices](#ux-best-practices)
11. [Troubleshooting](#troubleshooting)

---

## What is WhatsApp Flows?

**WhatsApp Flows** is a feature that allows businesses to create interactive, multi-step forms and surveys that customers can complete directly within WhatsApp. Unlike regular messages, Flows provide a structured, native experience for collecting information such as:

- 📝 Lead capture (name, email, phone)
- 📊 Customer surveys and feedback
- 📅 Appointment booking
- 🛒 Order forms and product selection
- 🎫 Registration and sign-ups

### How It Works

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Business      │    │   WhatsApp      │    │   Customer      │
│   (Sociovia)    │───▶│   (Meta)        │───▶│   (End User)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                      │                      │
        │  1. Create Flow      │                      │
        │─────────────────────▶│                      │
        │                      │                      │
        │  2. Publish Flow     │                      │
        │─────────────────────▶│                      │
        │                      │                      │
        │  3. Send Template    │  4. Display Flow    │
        │  with Flow Button    │─────────────────────▶│
        │                      │                      │
        │                      │  5. User Completes  │
        │◀─────────────────────│◀─────────────────────│
        │  6. Receive Data     │                      │
        │                      │                      │
```

### Key Benefits

| Feature | Benefit |
|---------|---------|
| **Native Experience** | Forms open within WhatsApp, no external links |
| **Higher Completion** | 3-5x better completion rates than web forms |
| **Structured Data** | Consistent, validated data collection |
| **Mobile Optimized** | Designed for smartphone users |
| **Rich Components** | Dropdowns, date pickers, radio buttons, etc. |

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        SOCIOVIA FRONTEND                        │
├─────────────────────────────────────────────────────────────────┤
│  FlowBuilder.tsx       │  ButtonEditor.tsx   │  flowComponents.ts│
│  (Create/Edit Flows)   │  (Template Buttons) │  (Component Lib)  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        SOCIOVIA BACKEND                         │
├─────────────────────────────────────────────────────────────────┤
│  flow_routes.py        │  flow_endpoint.py   │  flow_testing.py │
│  (CRUD APIs)           │  (Dynamic Flows)    │  (Preview/Test)  │
├─────────────────────────────────────────────────────────────────┤
│  flow_validator.py     │  flow_access.py     │  models.py       │
│  (JSON Validation)     │  (Access Control)   │  (Database)      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      META GRAPH API                             │
│  - POST /{waba_id}/flows (Create Flow)                          │
│  - POST /{flow_id}/assets (Upload JSON)                         │
│  - POST /{flow_id}/publish (Publish)                            │
└─────────────────────────────────────────────────────────────────┘
```

### Database Schema

```sql
TABLE: whatsapp_flows
├── id                 (PRIMARY KEY)
├── account_id         (FK → whatsapp_accounts)
├── name               (Flow name)
├── category           (LEAD_GEN, SURVEY, BOOKING, etc.)
├── flow_version       (Version number for cloning)
├── parent_flow_id     (Reference to original flow if cloned)
├── flow_json          (JSONB - the actual flow structure)
├── schema_version     (Meta schema version, e.g., "5.0")
├── entry_screen_id    (First screen to display)
├── meta_flow_id       (ID returned by Meta after publishing)
├── status             (DRAFT, PUBLISHED, DEPRECATED)
├── created_at         (Timestamp)
├── updated_at         (Timestamp)
└── published_at       (Timestamp when published to Meta)
```

---

## Implementation Phases

### Phase 1: MVP Static Flows ✅

**What was implemented:**
- `WhatsAppFlow` database model with versioning support
- Flow JSON validator with Meta's strict rules
- 9 CRUD API endpoints for flow management
- Visual Flow Builder UI with drag-and-drop components
- Live WhatsApp-style preview
- Database migration script

**Files:**
- `backend/whatsapp/models.py` - WhatsAppFlow model
- `backend/whatsapp/flow_validator.py` - Validation engine
- `backend/whatsapp/flow_routes.py` - API endpoints
- `frontend/src/whatsapp/pages/FlowBuilder.tsx` - UI Builder
- `backend/migrate_flows.py` - Database migration

---

### Phase 2: Template + Flow Attachment ✅

**What was implemented:**
- "Flow" button type added to template builder
- Fetches only PUBLISHED flows from same WhatsApp account
- Stores `flow_id` in button configuration
- Validation that template and flow belong to same WABA

**How it works:**
1. Create a template in Template Builder
2. Add a button with type "Flow"
3. Select a published flow from the dropdown
4. When customer clicks the button, the flow opens

**Files:**
- `frontend/src/whatsapp/components/builder/ButtonEditor.tsx`
- `frontend/src/whatsapp/utils/templateUtils.ts`

---

### Phase 3: Dynamic Flows (Endpoint-Powered) ✅

**What was implemented:**
- RSA-2048 encryption for secure data exchange
- Rate limiting (50 requests/minute)
- Idempotency cache to prevent duplicate processing
- Sample data handlers for:
  - Appointment slots
  - Product catalogs
  - Promo code validation

**How Dynamic Flows Work:**
```
Customer selects date → Meta calls your endpoint → 
You return available time slots → Customer sees options
```

**Files:**
- `backend/whatsapp/flow_endpoint.py`

---

### Phase 4: Multi-Business Isolation ✅

**What was implemented:**
- `@require_flow_access` decorator for route protection
- Workspace-based isolation (X-Workspace-ID header)
- WABA-scoped flow access
- Template-flow matching validation

**Security Rules:**
- Users can only access flows for their workspace
- Flows can only attach to templates from same WABA
- Published flows visible only within their account

**Files:**
- `backend/whatsapp/flow_access.py`

---

### Phase 5: Testing & Publishing ✅

**What was implemented:**
- `/preview` - Get flow preview data
- `/test-data` - Generate sample test data
- `/publish-check` - Pre-publish validation (6 checks)
- `/simulate` - Full flow simulation with data collection

**Pre-publish checks:**
1. Status is DRAFT
2. JSON is valid
3. Entry screen exists
4. Terminal screen exists
5. Access token is valid
6. Screen count ≤ 10

**Files:**
- `backend/whatsapp/flow_testing.py`

---

### Phase 6: UX Best Practices ✅

**What was implemented:**
- 6 Golden Rules for high-completion flows
- Component library with 30+ templates
- 3 complete pre-built flows:
  - Lead Capture (3 screens)
  - Customer Survey (4 screens)
  - Appointment Booking (5 screens)

**Files:**
- `frontend/src/whatsapp/utils/flowComponents.ts`

---

## Getting Started

### Prerequisites

1. **WhatsApp Business Account (WABA)** connected to Sociovia
2. **Backend running** on port 5000
3. **Frontend running** on port 8080
4. **Database migration** completed

### Step 1: Run Database Migration

```bash
cd backend/sociovia-28-11-2025-main/Sociovia
python migrate_flows.py
```

### Step 2: Start Backend

```bash
python test.py
```

### Step 3: Start Frontend

```bash
cd frontend/sociovia-launchpad-31-prabhu_4_12_2025
npm run dev
```

### Step 4: Access Flow Builder

Navigate to: `http://localhost:8080/dashboard/whatsapp/flows/new`

---

## Creating Your First Flow

### Using the Visual Builder

1. **Open Flow Builder**
   - Go to `/dashboard/whatsapp/flows/new`

2. **Set Flow Details**
   - Enter a name (e.g., "lead_capture_form")
   - Select category (Lead Generation, Survey, etc.)

3. **Build Screens**
   - Click "Add Screen" to create a new screen
   - Add components: Heading, Text Input, Dropdown, etc.
   - Set the Footer button to navigate to next screen

4. **Configure Entry Screen**
   - First screen is automatically the entry screen
   - Mark the last screen as "Terminal"

5. **Validate**
   - Real-time validation shows errors
   - Fix all errors before publishing

6. **Save & Publish**
   - Click "Save Draft" to save without publishing
   - Click "Publish to Meta" when ready

### Using the API

```bash
# Create a new flow
curl -X POST http://127.0.0.1:5000/api/whatsapp/flows \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": 12,
    "name": "My Lead Form",
    "category": "LEAD_GEN"
  }'

# Response includes flow_id and sample flow JSON
```

### Using Pre-built Templates

```javascript
import { flowTemplates } from '@/whatsapp/utils/flowComponents';

// Use the lead capture template
const leadCaptureFlow = flowTemplates.leadCapture;

// Or the customer survey template
const surveyFlow = flowTemplates.customerSurvey;

// Or appointment booking
const bookingFlow = flowTemplates.appointmentBooking;
```

---

## Sending Flows in Chats

There are **two ways** to send a flow to customers:

### Method 1: Via Template Button (Recommended)

1. **Create a Message Template** with a Flow button
2. **Attach your published flow** to the button
3. **Send the template** to customers
4. Customer clicks button → Flow opens

**Template Example:**
```json
{
  "name": "lead_collection",
  "category": "MARKETING",
  "components": [
    {
      "type": "BODY",
      "text": "Hi! We'd love to learn more about you."
    },
    {
      "type": "BUTTONS",
      "buttons": [
        {
          "type": "FLOW",
          "text": "Get Started",
          "flow_id": "123456789",  // Your published flow's Meta ID
          "flow_token": ""
        }
      ]
    }
  ]
}
```

### Method 2: Direct Flow Link (Interactive Message)

Send an interactive message with the flow:

```python
# Backend code to send flow
import requests

def send_flow_message(phone_number, flow_id, cta_text):
    response = requests.post(
        f"https://graph.facebook.com/v18.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "body": {"text": "Please fill out this form"},
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_id": flow_id,
                        "flow_cta": cta_text,
                        "flow_token": "optional-context-data"
                    }
                }
            }
        }
    )
    return response.json()
```

---

## Receiving & Storing Data

### Where Flow Responses Are Stored

When a customer completes a flow, the data is received via **webhooks**:

```
Customer Completes Flow
        │
        ▼
Meta Sends Webhook to Your Server
        │
        ▼
/api/whatsapp/webhook (in test.py)
        │
        ▼
Process & Store in Database
```

### Webhook Payload Example

When a flow is completed, Meta sends:

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "type": "interactive",
                "interactive": {
                  "type": "nfm_reply",
                  "nfm_reply": {
                    "response_json": "{\"full_name\":\"John Doe\",\"email\":\"john@example.com\",\"phone\":\"+1234567890\"}",
                    "body": "Sent",
                    "name": "flow"
                  }
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

### Processing Flow Responses

Add this to your webhook handler:

```python
# In your webhook route
@app.route('/api/whatsapp/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    
    for entry in data.get('entry', []):
        for change in entry.get('changes', []):
            messages = change.get('value', {}).get('messages', [])
            
            for message in messages:
                if message.get('type') == 'interactive':
                    interactive = message.get('interactive', {})
                    
                    if interactive.get('type') == 'nfm_reply':
                        # This is a flow response!
                        response_json = interactive['nfm_reply']['response_json']
                        flow_data = json.loads(response_json)
                        
                        # Store the data
                        save_flow_response(
                            phone=message['from'],
                            data=flow_data,
                            timestamp=message['timestamp']
                        )
    
    return 'OK', 200
```

### Storage Options

1. **CRM Leads Table** - For lead capture flows
2. **Survey Responses Table** - For feedback/surveys
3. **Appointments Table** - For booking flows
4. **Custom Table** - Based on your flow category

---

## API Reference

### Flow Management APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/whatsapp/flows` | Create new draft flow |
| GET | `/api/whatsapp/flows` | List flows (with filters) |
| GET | `/api/whatsapp/flows/{id}` | Get flow details |
| PUT | `/api/whatsapp/flows/{id}` | Update draft flow |
| DELETE | `/api/whatsapp/flows/{id}` | Delete draft flow |
| POST | `/api/whatsapp/flows/{id}/publish` | Publish to Meta |
| POST | `/api/whatsapp/flows/{id}/clone` | Clone flow |
| POST | `/api/whatsapp/flows/{id}/deprecate` | Deprecate published flow |
| POST | `/api/whatsapp/flows/validate` | Validate JSON |
| GET | `/api/whatsapp/flows/templates` | Get sample templates |

### Testing APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/whatsapp/flows/{id}/preview` | Get preview data |
| GET | `/api/whatsapp/flows/{id}/test-data` | Generate test data |
| GET | `/api/whatsapp/flows/{id}/publish-check` | Pre-publish validation |
| POST | `/api/whatsapp/flows/{id}/simulate` | Simulate completion |

### Dynamic Flow APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/whatsapp/flows/endpoint` | Handle Meta data exchange |
| POST | `/api/whatsapp/flows/keys/generate` | Generate RSA keys |
| GET | `/api/whatsapp/flows/health` | Health check |

---

## Security & Access Control

### Multi-Tenant Isolation

```python
# Access control decorator
@require_flow_access
def get_flow(flow_id):
    # Automatically validates:
    # 1. Flow exists
    # 2. User has access to flow's workspace
    # 3. Attaches flow to request context
    pass
```

### Encryption for Dynamic Flows

Dynamic flows use RSA-2048 encryption:

```bash
# Generate encryption keys for an account
curl -X POST http://127.0.0.1:5000/api/whatsapp/flows/keys/generate \
  -H "Content-Type: application/json" \
  -d '{"account_id": 12}'
```

### Rate Limiting

- 50 requests per minute per IP
- Idempotency cache prevents duplicate processing
- 2-second timeout for dynamic data requests

---

## UX Best Practices

### The 6 Golden Rules

1. **Keep It Short**
   - Max 5 screens for optimal completion
   - 3-4 input fields per screen
   - Complete in < 2 minutes

2. **Clear Progress**
   - Use "Step 1 of 3" in titles
   - Show estimated time remaining

3. **Smart Field Ordering**
   - Easy fields first (name, selection)
   - Sensitive fields later (phone, email)
   - Optional fields last

4. **Proper Validation**
   - Use input-type for auto-validation
   - Clear error messages
   - Don't block on optional fields

5. **Mobile-First Design**
   - Large touch targets (48px minimum)
   - No horizontal scrolling
   - Native input types

6. **Reduce Cognitive Load**
   - One question per screen
   - Use familiar patterns
   - Provide defaults where possible

### Component Examples

```typescript
import { inputComponents, selectionComponents } from '@/whatsapp/utils/flowComponents';

// Email field with validation
const emailField = inputComponents.emailField({ required: true });

// Rating scale (1-5 stars)
const ratingField = selectionComponents.ratingScale('rating', 'How was your experience?');

// Date picker
const dateField = inputComponents.dateField('appointment_date', 'Select a Date');
```

---

## Troubleshooting

### Common Issues

**1. "Account not found" when creating flow**
- Ensure account_id exists in whatsapp_accounts table
- Check that the account is active

**2. "Flow validation failed"**
- Check for banned keywords (password, credit card, etc.)
- Ensure entry screen exists
- Verify at least one terminal screen
- Max 10 screens allowed

**3. "Cannot publish flow"**
- Flow must be in DRAFT status
- Account must have valid access token
- Fix all validation errors first

**4. "Access denied: Flow belongs to different workspace"**
- Include correct X-Workspace-ID header
- Verify user has access to the workspace

**5. Flow not appearing in template button dropdown**
- Flow must be PUBLISHED (not DRAFT)
- Flow must belong to same account as template

### Debug Commands

```bash
# Check flow status
curl http://127.0.0.1:5000/api/whatsapp/flows/1

# Validate flow JSON
curl -X POST http://127.0.0.1:5000/api/whatsapp/flows/validate \
  -H "Content-Type: application/json" \
  -d '{"flow_json": {...}, "entry_screen_id": "WELCOME"}'

# Check publish readiness
curl http://127.0.0.1:5000/api/whatsapp/flows/1/publish-check

# Health check
curl http://127.0.0.1:5000/api/whatsapp/flows/health
```

---

## Related Documentation

- [Meta WhatsApp Flows Documentation](https://developers.facebook.com/docs/whatsapp/flows)
- [Flow JSON Reference](https://developers.facebook.com/docs/whatsapp/flows/reference/flowjson)
- [WhatsApp Business API](https://developers.facebook.com/docs/whatsapp/cloud-api)

---

*Last Updated: December 24, 2024*
