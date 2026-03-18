# SocioviaCrm/analytics.py
import functools
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import current_app, Blueprint, jsonify, Flask
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func

# If your project uses a different import path, adjust these
# We assume current_app.db is SQLAlchemy instance and current_app.crm_models contains CRM models
logger = logging.getLogger("sociovia.analytics")

bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")


def init_analytics_models():
    """Define analytics model and attach to current_app.crm_models for convenient access."""
    db = getattr(current_app, "db", None)
    if db is None:
        raise RuntimeError("app.db not found on current_app")

    # Avoid redefining if already present
    if getattr(current_app, "analytics_models_defined", False):
        return
    
    import uuid

    class CampaignMetric(db.Model):
        __tablename__ = "campaign_metrics"
        id = db.Column(db.String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
        workspace_id = db.Column(db.String, nullable=True, index=True)
        campaign_id = db.Column(db.String, nullable=False, index=True)  # your campaigns.id
        date = db.Column(db.Date, nullable=False, index=True)  # metrics for a date (or timestamp)
        impressions = db.Column(db.BigInteger, default=0)
        clicks = db.Column(db.BigInteger, default=0)
        spend = db.Column(db.Numeric(14,2), default=0)
        leads = db.Column(db.Integer, default=0)
        revenue = db.Column(db.Numeric(14,2), default=0)
        cpl = db.Column(db.Numeric(14,2), default=0)
        roas = db.Column(db.Numeric(14,2), default=0)
        raw_payload = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        __table_args__ = (
            db.UniqueConstraint("campaign_id", "date", name="uq_campaign_date"),
            {"extend_existing": True},
        )

    # Expose model for other modules
    current_app.analytics_model = CampaignMetric
    current_app.analytics_models_defined = True


def fetch_metrics_from_meta(account_id: str, since: datetime, until: datetime) -> List[Dict[str, Any]]:
    """
    Adapter to call your Meta wrapper and return a list of metric dicts.
    Each dict should at least include: campaign_id, date, impressions, clicks, spend, leads, revenue (optional).
    Replace / adapt this function to call your MetaHelpers wrapper.
    """
    logger.debug("fetch_metrics_from_meta: account_id=%s since=%s until=%s", account_id, since, until)
    try:
        # Prefer your real wrapper. Example:
        # resp = MetaHelpers.api_campaign_insights(account_id=account_id, since=since, until=until)
        # transform resp to required format and return list[...]

        # Fallback: mock empty list if MetaHelpers isn't available
        if hasattr(__import__("MetaHelpers"), "api_campaign_insights"):
            mh = __import__("MetaHelpers")
            # try common function names
            if hasattr(mh, "api_campaign_insights"):
                resp = mh.api_campaign_insights(account_id=account_id, since=since.isoformat(), until=until.isoformat())
                # resp -> convert to list of dicts expected by storage code
                # The conversion depends on your wrapper's output. Here's a generic attempt:
                metrics = []
                for row in resp.get("data", []) if isinstance(resp, dict) else resp:
                    metrics.append({
                        "campaign_id": row.get("campaign_id") or row.get("id") or row.get("campaign"),
                        "date": row.get("date") or row.get("day") or since.date().isoformat(),
                        "impressions": int(row.get("impressions", 0)),
                        "clicks": int(row.get("clicks", 0)),
                        "spend": float(row.get("spend", 0) or 0),
                        "leads": int(row.get("leads", row.get("actions", {}).get("leads", 0) if isinstance(row.get("actions"), dict) else 0)),
                        "revenue": float(row.get("revenue", 0) or 0),
                        "raw": row
                    })
                return metrics
        # If wrapper absent or unusable, return empty list
        return []
    except Exception as e:
        logger.exception("Error calling MetaHelpers: %s", e)
        return []


def upsert_metrics(metrics: List[Dict[str, Any]]):
    """
    Store metrics into campaign_metrics table. Upsert by campaign_id + date.
    """
    db = current_app.db
    CampaignMetric = current_app.analytics_model

    for m in metrics:
        try:
            # parse fields, be defensive
            campaign_id = str(m.get("campaign_id"))
            date_raw = m.get("date")
            if isinstance(date_raw, str):
                date_obj = datetime.fromisoformat(date_raw).date()
            elif isinstance(date_raw, datetime):
                date_obj = date_raw.date()
            else:
                # fallback to today
                date_obj = datetime.utcnow().date()

            # compute derived metrics
            impressions = int(m.get("impressions", 0) or 0)
            clicks = int(m.get("clicks", 0) or 0)
            spend = float(m.get("spend", 0) or 0)
            leads = int(m.get("leads", 0) or 0)
            revenue = float(m.get("revenue", 0) or 0)

            cpl = (spend / leads) if leads else 0
            roas = (revenue / spend) if spend else 0

            # try update existing
            existing = db.session.query(CampaignMetric).filter(
                CampaignMetric.campaign_id == campaign_id,
                CampaignMetric.date == date_obj
            ).one_or_none()

            payload = m.get("raw", m)

            if existing:
                existing.impressions = impressions
                existing.clicks = clicks
                existing.spend = spend
                existing.leads = leads
                existing.revenue = revenue
                existing.cpl = cpl
                existing.roas = roas
                existing.raw_payload = payload
                db.session.add(existing)
            else:
                row = CampaignMetric(
                    campaign_id=campaign_id,
                    date=date_obj,
                    impressions=impressions,
                    clicks=clicks,
                    spend=spend,
                    leads=leads,
                    revenue=revenue,
                    cpl=cpl,
                    roas=roas,
                    raw_payload=payload
                )
                db.session.add(row)

            db.session.commit()
        except SQLAlchemyError as sql_e:
            db.session.rollback()
            logger.exception("DB error upserting metric for campaign %s date %s: %s", m.get("campaign_id"), m.get("date"), sql_e)
        except Exception as e:
            logger.exception("Unexpected error upserting metric: %s", e)


def analytics_job_once(app: Flask, account_id: Optional[str] = None, hours_back: int = 4):
    """
    One run of the analytics sync job: fetch metrics for the last `hours_back` hours and store them.
    - app: Flask app object used to create app context for background thread
    - account_id: Meta account id (ad account) to fetch. If None, try config / db lookup.
    - hours_back: window to fetch (we fetch since=now-hours_back to now)
    """
    logger.info("Starting analytics_job_once; hours_back=%s account_id=%s", hours_back, account_id)
    # Use the passed app to create an application context on this thread
    with app.app_context():
        # ensure analytics model exists
        init_analytics_models()

        # determine account_id(s): either provided or from config or from SocialAccount model
        accts: List[str] = []
        if account_id:
            accts = [account_id]
        else:
            # try to find linked accounts in your models (if you have SocialAccount model)
            try:
                SocialAccount = current_app.__dict__.get("SocialAccount") or getattr(current_app, "SocialAccount", None)
            except Exception:
                SocialAccount = None

            if SocialAccount is not None:
                try:
                    # example: SocialAccount.store_ad_account_id or similar field
                    rows = current_app.db.session.query(SocialAccount).all()
                    for r in rows:
                        # adapt to your schema; common field names: account_id, ad_account_id, page_id
                        if getattr(r, "ad_account_id", None):
                            accts.append(r.ad_account_id)
                        elif getattr(r, "account_id", None):
                            accts.append(r.account_id)
                except Exception:
                    logger.debug("No social accounts found or failed to query SocialAccount")

            # fallback to config value
            cfg_account = current_app.config.get("FB_AD_ACCOUNT_ID")
            if cfg_account:
                accts.append(cfg_account)

        # dedupe
        accts = list(dict.fromkeys(accts))

        end = datetime.utcnow()
        start = end - timedelta(hours=hours_back)

        total_fetched = 0
        for acct in accts:
            try:
                metrics = fetch_metrics_from_meta(acct, since=start, until=end)
                if not metrics:
                    logger.info("No metrics returned for account %s in %s..%s", acct, start, end)
                    continue
                upsert_metrics(metrics)
                total_fetched += len(metrics)
            except Exception as e:
                logger.exception("Failed to fetch/store metrics for account %s: %s", acct, e)

        logger.info("Analytics job complete. total metrics processed: %d", total_fetched)


scheduler: Optional[BackgroundScheduler] = None


def start_scheduler(app: Flask):
    """
    Start the background scheduler. Call once from your entrypoint after app is created.
    """
    global scheduler
    if scheduler:
        logger.info("Scheduler already started")
        return scheduler

    # Use Flask config to customize schedule if needed
    interval_hours = int(app.config.get("ANALYTICS_FETCH_HOURS", 4))

    scheduler = BackgroundScheduler()
    # interval trigger every interval_hours
    scheduler.add_job(
        func=functools.partial(analytics_job_once, app, None, interval_hours),
        trigger=IntervalTrigger(hours=interval_hours),
        id="sociovia-analytics-fetch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=None  # set None so it runs on schedule; we add immediate job below
    )

    scheduler.start()
    logger.info("Analytics scheduler started (every %s hours)", interval_hours)

    # optional: run once immediately (in background)
    try:
        scheduler.add_job(
            func=functools.partial(analytics_job_once, app, None, interval_hours),
            id="sociovia-analytics-fetch-immediate",
            replace_existing=True
        )
    except Exception:
        logger.debug("Immediate analytics job schedule failed - maybe already scheduled")

    return scheduler


# ----- Flask route to trigger manual sync -----
@bp.route("/sync", methods=["POST"])
def manual_sync():
    """Trigger an on-demand analytics sync (non-blocking)."""
    try:
        # enqueue job in scheduler if running
        if scheduler:
            # capture concrete app object to use from background thread
            app_obj = current_app._get_current_object()
            scheduler.add_job(
                func=functools.partial(
                    analytics_job_once,
                    app_obj,
                    None,
                    int(app_obj.config.get("ANALYTICS_FETCH_HOURS", 4))
                ),
                id=f"manual-analytics-{datetime.utcnow().isoformat()}"
            )
            return jsonify({"ok": True, "message": "Analytics sync scheduled"}), 202
        else:
            # run synchronously (if scheduler not started)
            analytics_job_once(current_app._get_current_object(), None, int(current_app.config.get("ANALYTICS_FETCH_HOURS", 4)))
            return jsonify({"ok": True, "message": "Analytics sync run completed"}), 200
    except Exception as e:
        logger.exception("manual_sync failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
