# WhatsApp Tech Provider Onboarding — Manual QA Checklist

Use this checklist after deploying code changes. Run `python migrate_onboarding.py` on each environment before testing.

**Base URL:** `{API_BASE}/api/whatsapp`

---

## Prerequisites

- [ ] `WHATSAPP_CONFIG_ID`, `FB_APP_ID`, `FB_APP_SECRET` set in backend env
- [ ] Embedded Signup configured in Meta Developer Console (Tech Provider)
- [ ] `migrate_onboarding.py` executed on target database
- [ ] Frontend env: `VITE_FB_APP_ID`, `VITE_WHATSAPP_CONFIG_ID`

---

## Phase 2 / 9 — OAuth exchange gate

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1 | Successful full onboarding | Complete Embedded Signup with valid WABA + phone | `POST /connect/exchange` → `success: true`, `onboarding_status: ACTIVE`, `is_active: true` |
| 2 | OAuth without assets | Facebook login with account that has no WABA | `POST /oauth/facebook-login` → `success: false`, HTTP 422, `onboarding_status: WABA_NOT_VISIBLE` |
| 3 | Incomplete validation | Connect with missing asset sharing (test Meta sandbox) | `success: false`, HTTP 422, status ≠ ACTIVE, `is_active: false` |

---

## Phase 3 — Asset discovery

| # | Scenario | Expected status |
|---|----------|-----------------|
| 4 | No WABA visible | `WABA_NOT_VISIBLE` |
| 5 | WABA visible, no phone | `PHONE_NOT_VISIBLE` |
| 6 | Missing permissions | `FAILED` or `ASSET_SHARING_PENDING` |
| 7 | Multiple WABAs | Auto-connect picks preferred/first WABA (document which was chosen) |

---

## Phase 4 — App subscription

| # | Scenario | Expected |
|---|----------|----------|
| 8 | Subscription succeeds | `app_subscribed: true` in health |
| 9 | Subscription fails | `SUBSCRIPTION_PENDING`, background job retries |

---

## Phase 6 — Health API

```http
GET /api/whatsapp/health?workspace_id={WORKSPACE_ID}
```

| # | Check | Expected field |
|---|-------|----------------|
| 10 | Active account | `status: ACTIVE`, all visibility flags `true` |
| 11 | Pending account | `status` matches DB, `user_message` present |
| 12 | Missing token | `token_valid: false`, `RECONNECT_REQUIRED` |

---

## Phase 7 — Automatic revalidation

| # | Scenario | Expected |
|---|----------|----------|
| 13 | Account in `PENDING` after Meta propagation delay | Promoted to `ACTIVE` within ~2–20 min (120s job interval) |
| 14 | Stuck pending 10+ cycles (~20 min) | Escalates to `RECONNECT_REQUIRED` |

Env: `WA_ONBOARDING_REVALIDATION_SECONDS=120`, `WA_ONBOARDING_RECONNECT_AFTER_CYCLES=10`

---

## Phase 8 / 11 / 12 — Frontend UX

| # | Scenario | UI expected |
|---|----------|-------------|
| 15 | Settings with pending account | Onboarding progress panel (not full connected layout) |
| 16 | Health breakdown | Portfolio / WABA / phone / subscription check rows visible |
| 17 | `RECONNECT_REQUIRED` | Orange "Reconnect WhatsApp" button on Settings + Setup router |
| 18 | Setup router reconnect | Focused reconnect screen (not 4-tile grid) |
| 19 | Customer messages | Phase 12 copy shown in alerts/toasts (permissions, incomplete, WABA access) |
| 20 | Facebook login no WABA | No "WhatsApp Connected!" toast; manual setup prompt |

---

## Phase 10 — Failure handling

| # | Scenario | Expected |
|---|----------|----------|
| 21 | User closes popup midway | No exchange call; no ACTIVE account |
| 22 | Wrong Business Portfolio | Validation fails; status stored; error message shown |
| 23 | Validation failure | Account row may exist but `is_active: false` |

---

## Phase 11 — Reconnect flow

| # | Scenario | Expected |
|---|----------|----------|
| 24 | Manual reconnect from Settings | Reconnect button triggers Embedded Signup |
| 25 | Account card "Re-link" | Scrolls to and clicks reconnect button |
| 26 | Expired / revoked token | `RECONNECT_REQUIRED` after revalidation |

---

## Phase 13 — Edge cases

| # | Scenario | Notes |
|---|----------|-------|
| 27 | Existing WABA + existing phone | Should reach ACTIVE |
| 28 | New WABA + new phone | Full Embedded Signup path |
| 29 | Existing active accounts (migration) | Backfill → `onboarding_status: ACTIVE` |
| 30 | Webhook subscription failure | `SUBSCRIPTION_PENDING` → retry or reconnect |

---

## Debug endpoints (local/staging only)

```http
POST /api/whatsapp/onboarding/revalidate
Body: { "workspace_id": "..." }

GET /api/whatsapp/onboarding/accounts?workspace_id=...
```

---

## Success criteria (all must pass)

An account is **fully connected** only when:

- [ ] Portfolio visible
- [ ] WABA visible
- [ ] Phone number visible
- [ ] Permissions valid
- [ ] App subscribed
- [ ] `onboarding_status === ACTIVE`
- [ ] `is_active === true`
- [ ] Settings shows connected layout (not onboarding panel)

Any failure → pending, failed, or reconnect-required — never treated as connected in UI or messaging routes.
