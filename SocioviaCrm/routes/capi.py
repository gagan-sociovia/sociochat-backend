"""
CAPI Dashboard Backend Routes
Provides endpoints to power the CapiDashboard frontend using real CRM data.
"""

import json
import os
import requests
from urllib.parse import urlencode
from itsdangerous import URLSafeSerializer, BadSignature
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, redirect, jsonify, current_app
from sqlalchemy import func, desc, case
from models import db # Use the global db instance, or get it from current_app if needed

bp = Blueprint("capi", __name__)


def get_model(model_name):
    """Helper to retrieve a model class from current_app.crm_models"""
    if not hasattr(current_app, "crm_models") or model_name not in current_app.crm_models:
        raise RuntimeError(f"Model {model_name} not found in current_app.crm_models")
    return current_app.crm_models[model_name]


def _get_workspace_id():
    """Extract and validate workspace_id from query params."""
    ws = request.args.get("workspace_id")
    if not ws:
        return None
    try:
        return int(ws)
    except (ValueError, TypeError):
        return None


def _resolve_capi_credentials(workspace_id):
    """
    Centralized logic to resolve Pixel ID and Access Token.
    Includes discovery fallback if token exists but pixel is missing.
    """
    dataset_id = None
    access_token = None

    try:
        from models import CapiAccount, SocialAccount
        # 1. Primary: CapiAccount
        ca = CapiAccount.query.filter_by(workspace_id=workspace_id).first()
        if ca:
            dataset_id = ca.pixel_id
            access_token = ca.system_user_token

        # 2. Secondary: SocialAccount Fallback
        if not access_token or not dataset_id:
            sa = SocialAccount.query.filter_by(workspace_id=str(workspace_id)).order_by(SocialAccount.updated_at.desc()).first()
            if sa:
                if not access_token:
                    access_token = sa.access_token
                    current_app.logger.info(f"CAPI Creds: Using SocialAccount token for workspace {workspace_id}")
                if not dataset_id:
                    dataset_id = getattr(sa, 'pixel_id', None)
    except Exception as e:
        current_app.logger.error(f"Error resolving CAPI credentials: {e}")

    # 3. Tertiary: Event-based Fallback
    if not dataset_id:
        try:
            CapiEvent = get_model("CapiEvent")
            pixel_event = CapiEvent.query.filter_by(workspace_id=workspace_id)\
                .filter(CapiEvent.pixel_id.isnot(None))\
                .order_by(CapiEvent.event_time.desc()).first()
            if pixel_event and pixel_event.pixel_id:
                dataset_id = pixel_event.pixel_id
        except Exception:
            pass

    # 4. Discovery: If we have a token but NO pixel, try Graph API
    if access_token and not dataset_id:
        try:
            from MetaHelpers.GetWorkspaceData import discover_ad_accounts_for_token
            ad_accs = discover_ad_accounts_for_token(access_token)
            if ad_accs:
                aid = ad_accs[0]
                if not str(aid).startswith("act_"): aid = f"act_{aid}"
                v = os.environ.get("FB_API_VERSION", "v19.0")
                px_resp = requests.get(f"https://graph.facebook.com/{v}/{aid}/adspixels", 
                                     params={"access_token": access_token}, timeout=5)
                if px_resp.ok:
                    px_data = px_resp.json().get("data", [])
                    if px_data:
                        dataset_id = px_data[0]["id"]
                        current_app.logger.info(f"CAPI Creds: Discovered Pixel {dataset_id} via account {aid}")
        except Exception:
            pass

    # 5. Quaternary: Environment variables
    if not dataset_id:
        dataset_id = os.environ.get("FB_PIXEL_ID")
    if not access_token:
        access_token = os.environ.get("FB_CAPI_TOKEN")

    return dataset_id, access_token


# ------------------------------------------------------------------
# 1. Overview & Status
# ------------------------------------------------------------------
@bp.route("/overview", methods=["GET"])
def get_overview():
    """
    GET /api/capi/overview
    Fetches the overall health status, summary statistics, and volume trends.
    """
    workspace_id = _get_workspace_id()
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    try:
        CapiEvent = get_model("CapiEvent")
    except RuntimeError:
        return jsonify({"error": "CapiEvent model not initialized"}), 500

    base_q = CapiEvent.query.filter_by(workspace_id=workspace_id)

    # 1. Status & Last Event
    # First try to find properly sent events (with sent_at), then fallback to any event by event_time
    last_event = base_q.filter(CapiEvent.sent_at.isnot(None))\
        .order_by(CapiEvent.sent_at.desc()).first()
    
    if not last_event:
        # Fallback: get most recent event by event_time regardless of sent_at
        last_event = base_q.order_by(CapiEvent.event_time.desc()).first()
    
    status = "disconnected"
    last_event_sent_str = None
    
    if last_event:
        # Check sent_at first, then event_time as fallback
        last_time = last_event.sent_at or last_event.event_time
        
        if last_time and last_time > datetime.utcnow() - timedelta(hours=24):
            status = "connected"
        elif last_time:
            status = "idle"
        else:
            status = "disconnected"

        if last_event.sent_at:
            last_event_sent_str = last_event.sent_at.isoformat() + "Z"
        elif last_event.event_time:
            last_event_sent_str = last_event.event_time.isoformat() + "Z"

    # 2. Summary Stats (Total, Successful, Failed)
    # Statuses: 'sent' (success), 'failed', 'pending'
    stats = db.session.query(
        func.count(CapiEvent.id).label("total"),
        func.sum(case((CapiEvent.action_source == 'website', 1), else_=0)).label("browser_total"),
        func.sum(case((CapiEvent.action_source != 'website', 1), else_=0)).label("server_total"),
        func.sum(case((CapiEvent.status == 'sent', 1), else_=0)).label("successful"),
        func.sum(case((CapiEvent.status == 'failed', 1), else_=0)).label("failed")
    ).filter(CapiEvent.workspace_id == workspace_id).first()

    total = stats.total or 0
    successful = stats.successful or 0
    failed = stats.failed or 0
    browser_total = int(stats.browser_total or 0)
    server_total = int(stats.server_total or 0)
    
    success_rate = 0.0
    if total > 0:
        success_rate = round((successful / total) * 100, 1)

    # 3. Trend (Last 7 Days)
    # Group by date(event_time)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    # Use func.date(CapiEvent.event_time) for Postgres/SQLite
    # For compatibility, usually casting to date works
    daily_stats = db.session.query(
        func.date(CapiEvent.event_time).label("date"),
        func.count(CapiEvent.id).label("count")
    ).filter(CapiEvent.workspace_id == workspace_id, CapiEvent.event_time >= seven_days_ago)\
     .group_by(func.date(CapiEvent.event_time))\
     .order_by(func.date(CapiEvent.event_time)).all()

    trend = []
    trend_map = {str(row.date): row.count for row in daily_stats}
    
    base_date = datetime.utcnow() - timedelta(days=6) # 7 days including today
    for i in range(7):
        day_date = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        trend.append({
            "date": day_date,
            "value": trend_map.get(day_date, 0)
        })

    # 4. Resolve Dataset ID (Pixel ID) and Access Token
    dataset_id, access_token = _resolve_capi_credentials(workspace_id)

    # 5. Fetch Ad Account Insights (Spend, Purchases, ROAS)
    spend = 0.0
    meta_revenue = 0.0
    meta_purchases = 0
    ad_account_id = None
    
    try:
        from models import CapiAccount
        ca = CapiAccount.query.filter_by(workspace_id=workspace_id).first()
        if ca:
            ad_account_id = ca.ad_account_id
            
        if ad_account_id and access_token:
            if not str(ad_account_id).startswith("act_"):
                ad_account_id = f"act_{ad_account_id}"
            
            v = os.environ.get("FB_API_VERSION", "v19.0")
            # Fetch last 7 days insights
            insight_params = {
                "access_token": access_token,
                "date_preset": "last_7d",
                "fields": "spend,actions,action_values"
            }
            ins_resp = requests.get(f"https://graph.facebook.com/{v}/{ad_account_id}/insights", params=insight_params, timeout=10)
            if ins_resp.ok:
                ins_data = ins_resp.json().get("data", [])
                if ins_data:
                    day_data = ins_data[0]
                    spend = float(day_data.get("spend", 0.0))
                    
                    # Extract Purchases and Revenue from actions array
                    actions = day_data.get("actions", [])
                    for action in actions:
                        if action.get("action_type") == "purchase":
                            meta_purchases = int(float(action.get("value", 0)))
                            
                    action_values = day_data.get("action_values", [])
                    for val in action_values:
                        if val.get("action_type") == "purchase":
                            meta_revenue = float(val.get("value", 0.0))
    except Exception as e:
        current_app.logger.error(f"Error fetching Meta Insights for workspace {workspace_id}: {e}")

    # 6. Local Revenue Calculation (Optional fallback or combined)
    # Extract revenue from local CapiEvent records (Purchase events)
    local_revenue = 0.0
    local_purchases = 0
    try:
        purchase_events = base_q.filter(CapiEvent.event_name.ilike('purchase')).all()
        for pe in purchase_events:
            custom_data = pe.custom_data_json or {}
            val = custom_data.get("value") or custom_data.get("revenue")
            if val:
                try:
                    local_revenue += float(val)
                    local_purchases += 1
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    # Use Meta data primarily, fallback to local for revenue/purchases if Meta empty
    revenue = meta_revenue if meta_revenue > 0 else local_revenue
    purchases = meta_purchases if meta_purchases > 0 else local_purchases
    
    roas = (revenue / spend) if spend > 0 else 0.0
    cpa = (spend / purchases) if purchases > 0 else 0.0

    # Final check: If we have NO pixel AND NO token, force disconnected status
    if not dataset_id or not access_token:
        status = "disconnected"

    return jsonify({
        "status": status,
        "last_event_sent": last_event_sent_str,
        "dataset_id": dataset_id,
        "ad_account_id": ad_account_id,
        "summary": {
            "total_events": total,
            "browser_events": browser_total,
            "server_events": server_total,
            "successful_events": successful,
            "failed_events": failed,
            "success_rate": success_rate,
            "total_spend": spend,
            "total_revenue": revenue,
            "purchases": purchases,
            "roas": round(roas, 2),
            "cost_per_purchase": round(cpa, 2)
        },
        "trend": trend
    })


# ------------------------------------------------------------------
# 2. Event Breakdown
# ------------------------------------------------------------------
@bp.route("/events", methods=["GET"])
def get_events():
    """
    GET /api/capi/events?workspace_id=50
    Returns aggregated stats for each event type (Lead, Purchase, etc.).
    """
    workspace_id = _get_workspace_id()
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    try:
        CapiEvent = get_model("CapiEvent")
    except RuntimeError:
         return jsonify([]), 200

    # 1. Fetch Local Aggregated Events
    local_query = db.session.query(
        CapiEvent.event_name,
        func.count(CapiEvent.id).label("total"),
        func.sum(case((CapiEvent.action_source == 'website', 1), else_=0)).label("browser_count"),
        func.sum(case((CapiEvent.action_source != 'website', 1), else_=0)).label("server_count"),
        func.sum(case((CapiEvent.status == 'sent', 1), else_=0)).label("accepted"),
        func.sum(case((CapiEvent.status == 'failed', 1), else_=0)).label("failed"),
        func.max(CapiEvent.event_time).label("last_fired")
    ).filter(CapiEvent.workspace_id == workspace_id).group_by(CapiEvent.event_name).all()

    events = []
    for row in local_query:
        # Calculate local revenue for this event type
        event_revenue = 0.0
        try:
            # We fetch values specifically for this event type
            ev_records = CapiEvent.query.filter_by(workspace_id=workspace_id, event_name=row.event_name)\
                .filter(CapiEvent.custom_data_json.isnot(None)).all()
            for rec in ev_records:
                cd = rec.custom_data_json
                v = cd.get("value") or cd.get("revenue")
                if v:
                    try: event_revenue += float(v)
                    except: pass
        except Exception:
            pass

        events.append({
            "name": row.event_name,
            "sent": row.total,
            "browser_count": int(row.browser_count or 0),
            "server_count": int(row.server_count or 0),
            "accepted": int(row.accepted or 0),
            "failed": int(row.failed or 0),
            "revenue": round(event_revenue, 2),
            "last_fired": row.last_fired.isoformat() + "Z" if row.last_fired else None
        })

    # 2. If no local events, fallback to fetching real-time stats from Meta
    if not events:
        # Resolve credentials using centralized logic (includes discovery)
        pixel_id, access_token = _resolve_capi_credentials(workspace_id)

        if pixel_id and access_token:
            try:
                # Fetch aggregated stats from Meta for the last 7 days
                # Note: Meta keeps 'stats' for the pixel. aggregate=event gives count per event type.
                v = os.environ.get("FB_API_VERSION", "v19.0")
                params = {
                    "access_token": access_token,
                    "aggregation": "event",
                    "start_time": int((datetime.utcnow() - timedelta(days=7)).timestamp())
                }
                resp = requests.get(f"https://graph.facebook.com/{v}/{pixel_id}/stats", params=params, timeout=10)
                
                if resp.ok:
                    meta_data = resp.json().get("data", [])
                    # Meta returns buckets: [{"data": [{"value": "EventName", "count": N}, ...]}, ...]
                    # We need to aggregate counts by event name across all buckets
                    meta_aggregates = {}
                    
                    for bucket in meta_data:
                        bucket_events = bucket.get("data", [])
                        for item in bucket_events:
                            e_name = item.get("value")
                            e_count = item.get("count", 0)
                            if e_name and e_count > 0:
                                meta_aggregates[e_name] = meta_aggregates.get(e_name, 0) + e_count
                    
                    for e_name, total_count in meta_aggregates.items():
                        events.append({
                            "name": e_name,
                            "sent": total_count,
                            "browser_count": total_count, # Assume browser-only if fetching from pixel stats
                            "server_count": 0,
                            "accepted": total_count,
                            "failed": 0,
                            "last_fired": datetime.utcnow().isoformat() + "Z"
                        })
            except Exception as e:
                current_app.logger.error(f"Error fetching Meta Pixel stats: {e}")

    return jsonify(events)


# ------------------------------------------------------------------
# 3. Activity Logs
# ------------------------------------------------------------------
@bp.route("/logs", methods=["GET"])
def get_logs():
    """
    GET /api/capi/logs?workspace_id=50&limit=5
    Fetches recent individual event logs for the "Activity Log" table.
    """
    workspace_id = _get_workspace_id()
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    limit = request.args.get("limit", 5, type=int)
    offset = request.args.get("offset", 0, type=int)

    try:
        CapiEvent = get_model("CapiEvent")
    except RuntimeError:
         return jsonify({"logs": [], "total": 0}), 200

    base_q = CapiEvent.query.filter_by(workspace_id=workspace_id)
    total = base_q.count()
    
    logs_query = base_q.order_by(CapiEvent.event_time.desc())\
                                .offset(offset)\
                                .limit(limit).all()

    logs = []
    for log in logs_query:
        # Map source
        src = log.action_source
        if log.event_source_url:
             src = "Website" # simplify for UI if url present
        
        # Or read from custom data?
        # For now use action_source or 'System'
        
        logs.append({
            "id": log.event_id,
            "timestamp": log.event_time.isoformat() + "Z",
            "event": log.event_name,
            "source": src,
            "status": "Success" if log.status == 'sent' else "Failed" # UI expects 'Success'
        })

    return jsonify({
        "logs": logs,
        "total": total
    })



# ------------------------------------------------------------------
# Pixel Event Tracking
# ------------------------------------------------------------------

# Standard Meta Pixel events supported via CAPI
ALLOWED_PIXEL_EVENTS = {
    "PageView", "ViewContent", "AddToCart", "AddToWishlist",
    "InitiateCheckout", "AddPaymentInfo", "Purchase", "Lead",
    "CompleteRegistration", "Contact", "CustomizeProduct",
    "Donate", "FindLocation", "Schedule", "Search",
    "StartTrial", "SubmitApplication", "Subscribe",
}


@bp.route("/track", methods=["POST"])
def track_pixel_event():
    """
    POST /api/capi/track
    Accepts standard pixel / website events and sends them via Conversions API.

    Body JSON:
      event_name (str, required)   – One of ALLOWED_PIXEL_EVENTS
      workspace_id (str, required) – Workspace that owns the pixel
      event_source_url (str, required) – Page URL where the event occurred
      user_data (dict, optional)   – email, phone, fbc, fbp, etc.
      custom_data (dict, optional) – value, currency, content_ids, content_name, etc.
    """
    data = request.get_json(force=True)

    event_name = data.get("event_name", "").strip()
    workspace_id = str(data.get("workspace_id", "")).strip()
    event_source_url = (data.get("event_source_url") or "").strip()
    user_data = data.get("user_data") or {}
    custom_data = data.get("custom_data") or {}

    # ---- Validation ----
    if not event_name:
        return jsonify({"error": "event_name is required"}), 400

    if event_name not in ALLOWED_PIXEL_EVENTS:
        return jsonify({
            "error": f"Unsupported event_name '{event_name}'",
            "allowed": sorted(ALLOWED_PIXEL_EVENTS),
        }), 400

    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    if not event_source_url:
        return jsonify({"error": "event_source_url is required for pixel events"}), 400

    # ---- Auto-fill browser signals from HTTP request if missing ----
    if not user_data.get("client_ip_address") and not user_data.get("ip"):
        user_data["client_ip_address"] = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
        )

    if not user_data.get("client_user_agent") and not user_data.get("user_agent"):
        user_data["client_user_agent"] = request.headers.get("User-Agent", "")

    # ---- Send via CAPI service ----
    try:
        from SocioviaCrm.capi_service import send_capi_event

        result = send_capi_event(
            event_name=event_name,
            user_data_dict=user_data,
            custom_data_dict=custom_data,
            workspace_id=workspace_id,
            action_source="website",
            event_source_url=event_source_url,
        )

        if result.get("success"):
            return jsonify({
                "success": True,
                "event_id": result.get("event_id"),
                "event_name": event_name,
            }), 200
        else:
            error_msg = result.get("error", "Unknown error")
            # Provide a user-friendly message when pixel is not configured
            if "Missing FB_PIXEL_ID" in error_msg or "missing pixel" in error_msg.lower():
                error_msg = "Meta Pixel is not configured for this workspace. Go to Settings > Conversions API to add your Pixel ID and System User Token."
            return jsonify({
                "success": False,
                "error": error_msg,
                "event_name": event_name,
            }), 400

    except Exception as e:
        current_app.logger.error(f"[CAPI Track] Failed to send pixel event: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ------------------------------------------------------------------
# 4. CRM Event Mapping
# ------------------------------------------------------------------
@bp.route("/mapping", methods=["GET"])
def get_mapping():
    """
    GET /api/capi/mapping
    Returns the current configuration mapping CRM stages to Meta events.
    """
    # 1. Try to fetch from Settings
    try:
        Setting = get_model("Setting")
        mapping_setting = Setting.query.filter_by(name="capi_mapping_config").first()
        if mapping_setting and mapping_setting.value:
             return jsonify(json.loads(mapping_setting.value))
    except Exception:
        pass

    # 2. Return Default if not configured
    mappings = {
        "crm_events": [
            {
                "stage": "New Lead",
                "meta_event": "Lead",
                "status": "Active"
            },
            {
                "stage": "Qualified",
                "meta_event": "QualifiedLead",
                "status": "Active"
            },
            {
                "stage": "Trial Started",
                "meta_event": "Subscribe",
                "status": "Paused"
            },
            {
                "stage": "Payment Received",
                "meta_event": "Purchase",
                "status": "Active"
            },
            {
                "stage": "Won",
                "meta_event": "Purchase",
                "status": "Active"
            }
        ],
        "pixel_events": [
            {
                "event": "PageView",
                "description": "Fired when a user views any page",
                "status": "Active"
            },
            {
                "event": "ViewContent",
                "description": "Fired when a user views a product or content page",
                "status": "Active"
            },
            {
                "event": "AddToCart",
                "description": "Fired when a user adds an item to cart",
                "status": "Active"
            },
            {
                "event": "InitiateCheckout",
                "description": "Fired when a user starts checkout",
                "status": "Active"
            },
            {
                "event": "Purchase",
                "description": "Fired when a purchase is completed",
                "status": "Active"
            },
            {
                "event": "Lead",
                "description": "Fired when a lead form is submitted on website",
                "status": "Active"
            },
            {
                "event": "CompleteRegistration",
                "description": "Fired when a user completes registration",
                "status": "Active"
            },
            {
                "event": "Search",
                "description": "Fired when a user performs a search",
                "status": "Active"
            },
            {
                "event": "Contact",
                "description": "Fired when a user contacts the business",
                "status": "Active"
            },
            {
                "event": "Subscribe",
                "description": "Fired when a user subscribes to a service",
                "status": "Active"
            },
            {
                "event": "StartTrial",
                "description": "Fired when a user starts a free trial",
                "status": "Active"
            },
            {
                "event": "Schedule",
                "description": "Fired when a user schedules an appointment",
                "status": "Active"
            },
            {
                "event": "AddPaymentInfo",
                "description": "Fired when a user adds payment information",
                "status": "Active"
            },
            {
                "event": "AddToWishlist",
                "description": "Fired when a user adds an item to wishlist",
                "status": "Active"
            },
            {
                "event": "SubmitApplication",
                "description": "Fired when a user submits an application",
                "status": "Active"
            },
            {
                "event": "CustomizeProduct",
                "description": "Fired when a user customizes a product",
                "status": "Active"
            },
            {
                "event": "Donate",
                "description": "Fired when a donation is made",
                "status": "Active"
            },
            {
                "event": "FindLocation",
                "description": "Fired when a user searches for a location",
                "status": "Active"
            }
        ]
    }
    return jsonify(mappings)


# ------------------------------------------------------------------
# 5. Data Quality
# ------------------------------------------------------------------
@bp.route("/quality", methods=["GET"])
def get_quality():
    """
    GET /api/capi/quality?workspace_id=50
    Returns data quality metrics and identifier signal coverage.
    """
    workspace_id = _get_workspace_id()
    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    try:
        CapiEvent = get_model("CapiEvent")
        # Analyze last 100 events for this workspace
        recent_events = CapiEvent.query.filter_by(workspace_id=workspace_id)\
            .order_by(CapiEvent.created_at.desc()).limit(100).all()
    except RuntimeError:
         recent_events = []

    if not recent_events:
        return jsonify({
            "match_quality_score": 0,
            "data_completeness": 0,
            "identifier_signals": [
                {"label": "Email", "present": False},
                {"label": "Phone", "present": False},
                {"label": "IP", "present": False},
                {"label": "User Agent", "present": False}
            ]
        })

    # Calculate basic coverage stats
    total = len(recent_events)
    has_email = 0
    has_phone = 0
    has_ip = 0
    has_ua = 0
    
    for evt in recent_events:
        ud = evt.user_data_json or {}
        # user_data might use hashed keys like 'em', 'ph' or plain 'email', 'phone' depending on implementation
        # Meta CAPI uses: em, ph, client_ip_address, client_user_agent, fbc, fbp
        
        if ud.get("em") or ud.get("email"): has_email += 1
        if ud.get("ph") or ud.get("phone"): has_phone += 1
        if ud.get("client_ip_address") or ud.get("ip"): has_ip += 1
        if ud.get("client_user_agent") or ud.get("user_agent"): has_ua += 1

    # Simple score algorithm
    # Weight: Email(30), Phone(30), IP(20), UA(20)
    score = 0
    score += (has_email / total) * 30
    score += (has_phone / total) * 30
    score += (has_ip / total) * 20
    score += (has_ua / total) * 20
    
    # Completeness (average % of fields present per event)
    # Let's just key it to score for now
    
    return jsonify({
        "match_quality_score": int(score), # 0-100
        "data_completeness": int(score),   # Placeholder logic
        "identifier_signals": [
            {"label": "Email", "present": has_email > 0},
            {"label": "Phone", "present": has_phone > 0},
            {"label": "IP", "present": has_ip > 0},
            {"label": "User Agent", "present": has_ua > 0}
        ]
    })

# ------------------------------------------------------------------
# 6. Integration Docs & Snippet Generator
# ------------------------------------------------------------------

@bp.route("/integration-docs", methods=["GET"])
def integration_docs():
    """
    GET /api/capi/integration-docs?workspace_id=50
    Returns full integration documentation for the client's website.
    Includes the JS snippet, event reference, and setup instructions.
    """
    workspace_id = request.args.get("workspace_id")
    
    # Try to get the pixel_id for this workspace
    pixel_id = "YOUR_PIXEL_ID"
    api_endpoint = os.getenv("SOCIOVIA_API_URL", request.host_url.rstrip("/"))
    
    try:
        from models import CapiAccount
        account = None
        if workspace_id:
            # CapiAccount.workspace_id is Integer, so cast to int
            try:
                account = CapiAccount.query.filter_by(workspace_id=int(workspace_id)).first()
            except (ValueError, TypeError):
                account = CapiAccount.query.filter_by(workspace_id=workspace_id).first()
        
        # Fallback: find any connected account with a pixel_id
        if not account or not account.pixel_id:
            account = CapiAccount.query.filter(CapiAccount.pixel_id.isnot(None)).first()
        
        if account and account.pixel_id:
            pixel_id = account.pixel_id
            if not workspace_id:
                workspace_id = str(account.workspace_id)
    except Exception:
        pass

    # Build the JS embed snippet
    embed_snippet = _build_embed_snippet(pixel_id, workspace_id or "YOUR_WORKSPACE_ID", api_endpoint)

    return jsonify({
        "workspace_id": workspace_id,
        "pixel_id": pixel_id,
        "is_connected": pixel_id != "YOUR_PIXEL_ID",
        "embed_snippet": embed_snippet,
        "setup_steps": [
            {
                "step": 1,
                "title": "Connect Your Meta Account",
                "description": "Go to Settings → Conversions API and click 'Connect with Meta' to authorize your ad account and pixel.",
                "status": "completed" if pixel_id != "YOUR_PIXEL_ID" else "pending"
            },
            {
                "step": 2,
                "title": "Add the Tracking Snippet to Your Website",
                "description": "Copy the JavaScript snippet below and paste it into the <head> section of every page on your website. This enables both Meta Pixel (browser-side) and Conversions API (server-side) tracking.",
                "status": "pending"
            },
            {
                "step": 3,
                "title": "Track Custom Events",
                "description": "Use SocioviaTracker.track() in your website code to fire events like ViewContent, AddToCart, and Purchase when users take actions.",
                "status": "pending"
            },
            {
                "step": 4,
                "title": "Verify in Meta Events Manager",
                "description": "Go to Meta Events Manager → Test Events tab to confirm events are being received from both 'Browser' and 'Server' sources.",
                "status": "pending"
            }
        ],
        "event_reference": [
            {
                "event": "PageView",
                "description": "Fires automatically on every page load",
                "auto": True,
                "custom_data_fields": [],
                "example": "Automatic — no code needed"
            },
            {
                "event": "ViewContent",
                "description": "When a user views a product, article, or key page",
                "auto": False,
                "custom_data_fields": ["content_name", "content_ids", "content_type", "value", "currency"],
                "example": "SocioviaTracker.track('ViewContent', { content_name: 'Blue T-Shirt', content_ids: ['SKU123'], value: 1500, currency: 'INR' })"
            },
            {
                "event": "AddToCart",
                "description": "When a user adds an item to their cart",
                "auto": False,
                "custom_data_fields": ["content_ids", "content_type", "value", "currency", "num_items"],
                "example": "SocioviaTracker.track('AddToCart', { content_ids: ['SKU123'], value: 1500, currency: 'INR', num_items: 1 })"
            },
            {
                "event": "InitiateCheckout",
                "description": "When a user starts the checkout process",
                "auto": False,
                "custom_data_fields": ["value", "currency", "num_items"],
                "example": "SocioviaTracker.track('InitiateCheckout', { value: 3000, currency: 'INR', num_items: 2 })"
            },
            {
                "event": "Purchase",
                "description": "When a purchase is completed",
                "auto": False,
                "custom_data_fields": ["value", "currency", "content_ids", "num_items"],
                "example": "SocioviaTracker.track('Purchase', { value: 3000, currency: 'INR', content_ids: ['SKU123'] })"
            },
            {
                "event": "Lead",
                "description": "When a user submits a lead/contact form",
                "auto": False,
                "custom_data_fields": ["value", "currency"],
                "example": "SocioviaTracker.track('Lead', { value: 500, currency: 'INR' })"
            },
            {
                "event": "CompleteRegistration",
                "description": "When a user completes a signup/registration",
                "auto": False,
                "custom_data_fields": ["value", "currency"],
                "example": "SocioviaTracker.track('CompleteRegistration', { value: 0 })"
            },
            {
                "event": "Search",
                "description": "When a user performs a search",
                "auto": False,
                "custom_data_fields": ["search_string"],
                "example": "SocioviaTracker.track('Search', { search_string: 'blue t-shirt' })"
            },
            {
                "event": "Contact",
                "description": "When a user contacts the business (call, chat, email)",
                "auto": False,
                "custom_data_fields": [],
                "example": "SocioviaTracker.track('Contact')"
            },
            {
                "event": "Subscribe",
                "description": "When a user subscribes to a service or newsletter",
                "auto": False,
                "custom_data_fields": ["value", "currency"],
                "example": "SocioviaTracker.track('Subscribe', { value: 999, currency: 'INR' })"
            },
            {
                "event": "StartTrial",
                "description": "When a user starts a free trial",
                "auto": False,
                "custom_data_fields": ["value", "currency"],
                "example": "SocioviaTracker.track('StartTrial', { value: 0 })"
            },
            {
                "event": "Schedule",
                "description": "When a user schedules an appointment or booking",
                "auto": False,
                "custom_data_fields": [],
                "example": "SocioviaTracker.track('Schedule')"
            },
            {
                "event": "AddPaymentInfo",
                "description": "When a user adds payment information",
                "auto": False,
                "custom_data_fields": ["value", "currency"],
                "example": "SocioviaTracker.track('AddPaymentInfo')"
            },
            {
                "event": "AddToWishlist",
                "description": "When a user adds an item to wishlist",
                "auto": False,
                "custom_data_fields": ["content_ids", "content_name", "value", "currency"],
                "example": "SocioviaTracker.track('AddToWishlist', { content_ids: ['SKU123'], content_name: 'Blue T-Shirt' })"
            }
        ],
        "how_it_works": {
            "title": "How Browser Pixel + Server-Side CAPI Work Together",
            "description": "The snippet fires each event twice — once from the browser (Meta Pixel) and once from your server (Conversions API). Meta automatically deduplicates using the event_id, so each action is counted only once. This gives you maximum data reliability even when ad blockers are active.",
            "flow": [
                "User visits your website",
                "Browser Pixel fires the event directly to Meta (can be blocked by ad blockers)",
                "Simultaneously, the snippet sends the same event to Sociovia's CAPI endpoint",
                "Sociovia forwards the event server-side to Meta (bypasses ad blockers)",
                "Meta deduplicates both events using the shared event_id",
                "Result: 95-100% event capture rate vs ~60-70% with browser pixel alone"
            ],
            "discriminators": {
                "browser_pixel": {
                    "source": "Browser",
                    "indicator": "Sent via fbq() JavaScript call",
                    "visible_in": "Meta Events Manager → Source column shows 'Browser'"
                },
                "conversions_api": {
                    "source": "Server",
                    "indicator": "Sent via POST /api/capi/track with action_source='website'",
                    "visible_in": "Meta Events Manager → Source column shows 'Server'"
                },
                "crm_events": {
                    "source": "Server (CRM)",
                    "indicator": "Triggered by CRM actions (lead creation, deal won) with action_source='system_generated'",
                    "visible_in": "Meta Events Manager → Source column shows 'Server', custom_data contains event_source='crm'"
                }
            }
        },
        "meta_signals": {
            "title": "How to Know If a Visitor Came from Meta Ads",
            "signals": [
                {
                    "signal": "fbclid",
                    "type": "URL Parameter",
                    "description": "Meta appends ?fbclid=xxx to the landing page URL when someone clicks an ad. This is the primary click identifier.",
                    "example": "https://yoursite.com/product?fbclid=IwAR3abc123..."
                },
                {
                    "signal": "fbc",
                    "type": "Cookie (_fbc)",
                    "description": "The _fbc cookie is automatically set by the Meta Pixel from the fbclid parameter. Format: fb.1.{timestamp}.{fbclid}. This persists the click attribution across pages.",
                    "example": "fb.1.1709459200.IwAR3abc123"
                },
                {
                    "signal": "fbp",
                    "type": "Cookie (_fbp)",
                    "description": "The _fbp cookie is a browser identifier set by Meta Pixel on first visit. It identifies the browser even without an ad click. Format: fb.1.{timestamp}.{random}.",
                    "example": "fb.1.1709459200.1234567890"
                },
                {
                    "signal": "utm_source=facebook",
                    "type": "URL Parameter",
                    "description": "If your Meta ads use UTM tracking, utm_source=facebook identifies Meta as the traffic source.",
                    "example": "https://yoursite.com/?utm_source=facebook&utm_medium=paid"
                }
            ],
            "detection_logic": "If 'fbclid' is in the URL OR '_fbc' cookie exists → the visitor came from a Meta ad. The snippet automatically captures and forwards these signals."
        }
    })


@bp.route("/snippet", methods=["GET"])
def get_snippet():
    """
    GET /api/capi/snippet?workspace_id=50
    Returns just the embeddable JavaScript snippet for copy-paste.
    """
    workspace_id = request.args.get("workspace_id")
    pixel_id = "YOUR_PIXEL_ID"
    api_endpoint = os.getenv("SOCIOVIA_API_URL", request.host_url.rstrip("/"))

    if workspace_id:
        try:
            from models import CapiAccount
            try:
                account = CapiAccount.query.filter_by(workspace_id=int(workspace_id)).first()
            except (ValueError, TypeError):
                account = CapiAccount.query.filter_by(workspace_id=workspace_id).first()
            if account and account.pixel_id:
                pixel_id = account.pixel_id
        except Exception:
            pass
            
    # Fallback to Settings if missed
    if pixel_id == "YOUR_PIXEL_ID":
        try:
            Setting = get_model("Setting")
            pixel_setting = Setting.query.filter(Setting.name.in_(["fb_pixel_id", "pixel_id", "meta_pixel_id"])).first()
            if pixel_setting and pixel_setting.value:
                pixel_id = pixel_setting.value
        except Exception:
            pass

    # Fallback: find any connected account
    if pixel_id == "YOUR_PIXEL_ID":
        try:
            from models import CapiAccount
            account = CapiAccount.query.filter(CapiAccount.pixel_id.isnot(None)).first()
            if account and account.pixel_id:
                pixel_id = account.pixel_id
                if not workspace_id:
                    workspace_id = str(account.workspace_id)
        except Exception:
            pass

    snippet = _build_embed_snippet(pixel_id, workspace_id or "YOUR_WORKSPACE_ID", api_endpoint)

    return jsonify({
        "workspace_id": workspace_id,
        "pixel_id": pixel_id,
        "snippet": snippet,
    })


def _build_embed_snippet(pixel_id, workspace_id, api_endpoint):
    """Generates the JavaScript embed snippet for the client's website."""
    return f"""<!-- Sociovia Tracking: Meta Pixel + Conversions API -->
<script>
  // --- 1. Meta Pixel Base Code ---
  !function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;
  n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,
  document,'script','https://connect.facebook.net/en_US/fbevents.js');
  fbq('init', '{pixel_id}');
  fbq('track', 'PageView');

  // --- 2. Sociovia CAPI Tracker ---
  (function() {{
    var SOCIOVIA_CONFIG = {{
      apiEndpoint: '{api_endpoint}/api/capi/track',
      workspaceId: '{workspace_id}',
      pixelId: '{pixel_id}'
    }};

    function getCookie(name) {{
      var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
      return match ? decodeURIComponent(match[2]) : null;
    }}

    function getUrlParam(name) {{
      var params = new URLSearchParams(window.location.search);
      return params.get(name);
    }}

    function generateEventId() {{
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {{
        var r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      }});
    }}

    function sendCapiEvent(eventName, customData, userData) {{
      var fbclid = getUrlParam('fbclid');
      var fbc = getCookie('_fbc') || (fbclid ? 'fb.1.' + Date.now() + '.' + fbclid : null);
      var fbp = getCookie('_fbp');
      var eventId = generateEventId();

      // Fire browser pixel with event_id for dedup
      if (typeof fbq === 'function') {{
        fbq('track', eventName, customData || {{}}, {{ eventID: eventId }});
      }}

      // Fire server-side CAPI
      var payload = {{
        event_name: eventName,
        workspace_id: SOCIOVIA_CONFIG.workspaceId,
        event_source_url: window.location.href,
        event_id: eventId,
        user_data: Object.assign({{
          fbc: fbc,
          fbp: fbp
        }}, userData || {{}}),
        custom_data: customData || {{}}
      }};

      // Use sendBeacon for reliability (works even on page unload)
      if (navigator.sendBeacon) {{
        navigator.sendBeacon(SOCIOVIA_CONFIG.apiEndpoint,
          new Blob([JSON.stringify(payload)], {{ type: 'application/json' }}));
      }} else {{
        fetch(SOCIOVIA_CONFIG.apiEndpoint, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
          keepalive: true
        }});
      }}
    }}

    // Auto-fire PageView on load
    sendCapiEvent('PageView');

    // Expose global tracker for custom events
    window.SocioviaTracker = {{
      track: function(eventName, customData, userData) {{
        sendCapiEvent(eventName, customData, userData);
      }},
      identify: function(userData) {{
        window._socioviaUserData = userData;
      }}
    }};
  }})();
</script>
<noscript><img height="1" width="1" style="display:none"
  src="https://www.facebook.com/tr?id={pixel_id}&ev=PageView&noscript=1" /></noscript>
<!-- End Sociovia Tracking -->"""


# ------------------------------------------------------------------
# 7. CAPI OAuth & Token Management
# ------------------------------------------------------------------

FB_API_VERSION = "v19.0"
GRAPH = f"https://graph.facebook.com/{FB_API_VERSION}"
LOGIN_FOR_BUSINESS_CONFIG_ID = "949721094209106"

def get_serializer():
    STATE_SECRET = os.getenv("STATE_SIGNER_SECRET", "dev-secret")
    STATE_SALT = "sociovia-capi-meta-business"
    return URLSafeSerializer(STATE_SECRET, salt=STATE_SALT)

@bp.route("/oauth/connect", methods=["GET"])
def oauth_connect():
    """
    GET /api/capi/oauth/connect
    Initiates Facebook Login for Business flow for Conversions API.
    """
    workspace_id = request.args.get("workspace_id")
    user_id = request.args.get("user_id")

    if not workspace_id:
        return jsonify({"error": "workspace_id is required"}), 400

    state_payload = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    state = get_serializer().dumps(state_payload)

    FB_APP_ID = os.getenv("FB_APP_ID")
    _base = os.getenv("APP_BASE_URL", "https://sociovia-backend-362038465411.europe-west1.run.app")
    META_REDIRECT_URI = os.getenv("META_CAPI_REDIRECT_URI", os.getenv("META_REDIRECT_URI", f"{_base}/api/capi/oauth/callback"))

    params = {
        "client_id": FB_APP_ID,
        "redirect_uri": META_REDIRECT_URI,
        "response_type": "code",
        "state": state,
        "config_id": LOGIN_FOR_BUSINESS_CONFIG_ID,
    }

    url = f"https://www.facebook.com/{FB_API_VERSION}/dialog/oauth?{urlencode(params)}"
    current_app.logger.info(f"[CAPI OAUTH] Initiating connect for Workspace {workspace_id}")
    return redirect(url)


@bp.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """
    GET /api/capi/oauth/callback
    Exchanges code for client token and fetches business assets.
    """
    code = request.args.get("code")
    raw_state = request.args.get("state")
    error = request.args.get("error")

    if error:
        current_app.logger.error(f"[CAPI OAUTH ERROR] {error}")
        return jsonify({"success": False, "error": error}), 400

    if not code or not raw_state:
        return jsonify({"success": False, "error": "missing_code_or_state"}), 400

    try:
        state = get_serializer().loads(raw_state)
    except BadSignature:
        return jsonify({"success": False, "error": "invalid_state"}), 400

    workspace_id = state["workspace_id"]
    user_id = state.get("user_id")

    FB_APP_ID = os.getenv("FB_APP_ID")
    FB_APP_SECRET = os.getenv("FB_APP_SECRET")
    _base = os.getenv("APP_BASE_URL", "https://sociovia-backend-362038465411.europe-west1.run.app")
    META_REDIRECT_URI = os.getenv("META_CAPI_REDIRECT_URI", os.getenv("META_REDIRECT_URI", f"{_base}/api/capi/oauth/callback"))

    # Exchange CODE -> CLIENT USER TOKEN
    token_resp = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "client_id": FB_APP_ID,
            "client_secret": FB_APP_SECRET,
            "redirect_uri": META_REDIRECT_URI,
            "code": code,
        },
        timeout=10,
    ).json()

    if "error" in token_resp:
        return jsonify(token_resp), 400

    client_user_token = token_resp["access_token"]

    # 1. Get client business ID
    biz_res = requests.get(
        f"{GRAPH}/me",
        params={"fields": "client_business_id", "access_token": client_user_token},
        timeout=10,
    ).json()

    client_business_id = biz_res.get("client_business_id")
    if not client_business_id:
        return jsonify({"success": False, "error": "client_business_id_missing", "details": biz_res}), 400

    # 2. Get ad accounts client granted
    ad_accounts_map = {}
    try:
        ad_res = requests.get(
            f"{GRAPH}/{client_business_id}/client_ad_accounts",
            params={
                "fields": "account_id,name,account_status",
                "access_token": client_user_token,
                "limit": 200,
            },
            timeout=10,
        ).json()
        for a in ad_res.get("data", []):
            aid = str(a.get("account_id") or a.get("id", "")).replace("act_", "")
            if aid:
                ad_accounts_map[aid] = {
                    "id": aid,
                    "name": a.get("name"),
                    "status": a.get("account_status"),
                    "type": "business"
                }
    except Exception as e:
        current_app.logger.error(f"[CAPI OAUTH] Biz ad account fetch failed: {e}")

    # B. Fetch Personal Ad Accounts (Fallback/Discovery)
    try:
        me_ad_res = requests.get(
            f"{GRAPH}/me/adaccounts",
            params={
                "fields": "account_id,id,name,account_status",
                "access_token": client_user_token,
                "limit": 200,
            },
            timeout=10,
        ).json()
        for a in me_ad_res.get("data", []):
            aid = str(a.get("account_id") or a.get("id", "")).replace("act_", "")
            if aid and aid not in ad_accounts_map:
                ad_accounts_map[aid] = {
                    "id": aid,
                    "name": a.get("name"),
                    "status": a.get("account_status"),
                    "type": "personal"
                }
    except Exception as e:
        current_app.logger.error(f"[CAPI OAUTH] Personal ad account fetch failed: {e}")

    ad_accounts = list(ad_accounts_map.values())

    import base64
    oauth_json = json.dumps({
        "success": True,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "client_business_id": client_business_id,
        "client_user_token": client_user_token,
        "ad_accounts": ad_accounts,
    })
    encoded_data = base64.b64encode(oauth_json.encode('utf-8')).decode('utf-8')
    FRONTEND_URL = "http://127.0.0.1:8080"
    redirect_url = f"{FRONTEND_URL}/settings/capi/callback?data={encoded_data}"
    
    # Use HTML meta/js redirect instead of 302 to prevent browser hash issues 
    html = f"""
    <html>
      <head>
        <meta http-equiv="refresh" content="0; url={redirect_url}" />
        <script>window.location.href = "{redirect_url}";</script>
      </head>
      <body>Redirecting back to Sociovia...</body>
    </html>
    """
    return html
@bp.route("/oauth/save", methods=["POST"])
def oauth_save():
    """
    POST /api/capi/oauth/save
    Mints System User Token and saves CapiAccount settings.
    """
    data = request.get_json(force=True)
    workspace_id = str(data.get("workspace_id"))
    user_id = data.get("user_id")
    ad_account_id = str(data.get("ad_account_id", "")).replace("act_", "")
    client_business_id = data.get("client_business_id")
    client_user_token = data.get("client_user_token") # Used to mint the system token
    pixel_id = data.get("pixel_id") # Optional: Frontend can extract pixel from ad account

    if not all([workspace_id, ad_account_id, client_business_id, client_user_token]):
        return jsonify({"error": "missing_parameters"}), 400

    try:
        CapiAccount = get_model("CapiAccount")
    except RuntimeError:
        return jsonify({"error": "CapiAccount model not initialized"}), 500

    import hmac
    import hashlib
    
    FB_APP_SECRET = os.getenv("FB_APP_SECRET")
    
    # In the Facebook Login for Business flow, the client_user_token returned 
    # from the code exchange IS the permanent system user token.
    system_user_token = client_user_token

    # If pixel_id was not provided, we can fetch it using the ad account and the new token
    if not pixel_id:
        system_appsecret_proof = hmac.new(
            FB_APP_SECRET.encode('utf-8'),
            msg=system_user_token.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()

        pixel_res = requests.get(
            f"{GRAPH}/act_{ad_account_id}/adspixels",
            params={
                "access_token": system_user_token,
                "fields": "id,name",
                "appsecret_proof": system_appsecret_proof
            },
            timeout=10,
        ).json()
        pixels = pixel_res.get("data", [])
        if pixels:
            pixel_id = pixels[0]["id"]

    # Save to Database
    account = CapiAccount.query.filter_by(workspace_id=workspace_id).first()
    if not account:
        account = CapiAccount(workspace_id=workspace_id)
        db.session.add(account)

    account.user_id = user_id
    account.ad_account_id = ad_account_id
    account.client_business_id = client_business_id
    account.system_user_token = system_user_token
    account.pixel_id = pixel_id
    
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "CAPI Configuration Saved",
        "pixel_id": pixel_id
    })
