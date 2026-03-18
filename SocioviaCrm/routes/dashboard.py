# crm_management/routes/dashboard.py
from flask import Blueprint, jsonify, request, current_app
from datetime import datetime, timedelta

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s) if "T" in s else datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None
    
def resolve_date_range(start_raw, end_raw):
    now = datetime.utcnow()

    # Handle "undefined"
    if start_raw in (None, "", "undefined"):
        start_raw = None
    if end_raw in (None, "", "undefined"):
        end_raw = None

    # Handle presets like 7d, 30d, 90d
    if start_raw and start_raw.endswith("d"):
        try:
            days = int(start_raw[:-1])
            return now - timedelta(days=days), now
        except ValueError:
            pass
    elif start_raw and start_raw.endswith("m"):
        try:
            months = int(start_raw[:-1])
            # Approx 30 days per month
            return now - timedelta(days=months*30), now
        except ValueError:
            pass

    start = parse_date(start_raw)
    end = parse_date(end_raw)

    if start and end and end.hour == 0:
        end += timedelta(days=1)

    return start, end

@bp.route("/stats", methods=["GET"])
def stats():
    db = current_app.db
    models = current_app.crm_models
    Lead = models["Lead"]
    Campaign = models["Campaign"]

    workspace_id = request.args.get("workspace_id")  # String type in DB
    user_id = request.args.get("user_id", type=int)

    if not workspace_id or not user_id:
        return jsonify({"error": "workspace_id and user_id required"}), 400

    start, end = resolve_date_range(
        request.args.get("startDate"),
        request.args.get("endDate")
    )

    # -------------------------
    # Leads
    # -------------------------
    leads_q = db.session.query(Lead).filter(
        Lead.workspace_id == workspace_id
    )

    if start:
        leads_q = leads_q.filter(Lead.created_at >= start)
    if end:
        leads_q = leads_q.filter(Lead.created_at < end)

    total_leads = leads_q.count()
    active_leads = leads_q.filter(Lead.status != "closed").count()

    # -------------------------
    # Campaign metrics
    # -------------------------
    campaign_q = db.session.query(Campaign).filter(
        Campaign.workspace_id == workspace_id
    )

    if start:
        campaign_q = campaign_q.filter(Campaign.created_at >= start)
    if end:
        campaign_q = campaign_q.filter(Campaign.created_at < end)

    revenue = float(
        campaign_q.with_entities(
            db.func.coalesce(db.func.sum(Campaign.revenue), 0)
        ).scalar()
    )

    link_clicks = int(
        campaign_q.with_entities(
            db.func.coalesce(db.func.sum(Campaign.clicks), 0)
        ).scalar()
    )

    conversion_rate = (
        (total_leads / link_clicks) * 100 if link_clicks > 0 else 0.0
    )

    return jsonify({
        "revenue": {"value": revenue, "change": 0.0, "trend": "up"},
        "active_leads": {"value": active_leads, "change": 0.0, "trend": "up"},
        "conversion_rate": {"value": round(conversion_rate, 2), "change": 0.0, "trend": "up"},
        "link_clicks": {"value": link_clicks, "change": 0.0, "trend": "up"}
    })

@bp.route("/charts/revenue", methods=["GET"])
def revenue_chart():
    db = current_app.db
    Campaign = current_app.crm_models["Campaign"]

    workspace_id = request.args.get("workspace_id")  # String type in DB
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    start, end = resolve_date_range(
        request.args.get("startDate"),
        request.args.get("endDate")
    )

    q = (
        db.session.query(
            db.func.date(Campaign.created_at).label("day"),
            db.func.coalesce(db.func.sum(Campaign.revenue), 0)
        )
        .filter(Campaign.workspace_id == workspace_id)
    )

    if start:
        q = q.filter(Campaign.created_at >= start)
    if end:
        q = q.filter(Campaign.created_at < end)

    rows = (
        q
        .group_by("day")
        .order_by("day")
        .all()
    )

    return jsonify([
        {"date": day.strftime("%a"), "value": float(value)}
        for day, value in rows
    ])

@bp.route("/charts/sources", methods=["GET"])
def sources_chart():
    db = current_app.db
    Lead = current_app.crm_models["Lead"]

    workspace_id = request.args.get("workspace_id")  # String type in DB
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400

    rows = (
        db.session.query(
            Lead.source,
            db.func.count(Lead.id)
        )
        .filter(Lead.workspace_id == workspace_id)
        .group_by(Lead.source)
        .all()
    )

    return jsonify([
        {"name": source or "Unknown", "value": int(count)}
        for source, count in rows
    ])
