"""
Analytics Actions — summary, trends, export.
"""

import logging
from typing import Dict, Any, List

from agent_backend.action_registry import BaseAction, action_registry

logger = logging.getLogger(__name__)


@action_registry.register(domain="analytics", action="summary")
class AnalyticsSummaryAction(BaseAction):
    name = "analytics_summary"
    description = "Show analytics summary (messages sent, delivered, read, etc.)"
    required_params: List[str] = []
    optional_params = ["days", "account_id"]

    def execute(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        from whatsapp.models import WhatsAppMessage, WhatsAppAccount
        from sqlalchemy import func
        from datetime import datetime, timezone, timedelta
        from models import db

        workspace_id = context.get("workspace_id")
        account_id = context.get("account_id")
        days = int(params.get("days", 7))

        if not account_id:
            return {"status": "error", "message": "No WhatsApp account connected."}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            total = WhatsAppMessage.query.filter(
                WhatsAppMessage.account_id == account_id,
                WhatsAppMessage.created_at >= cutoff,
            ).count()

            sent = WhatsAppMessage.query.filter(
                WhatsAppMessage.account_id == account_id,
                WhatsAppMessage.direction == "outgoing",
                WhatsAppMessage.created_at >= cutoff,
            ).count()

            received = WhatsAppMessage.query.filter(
                WhatsAppMessage.account_id == account_id,
                WhatsAppMessage.direction == "incoming",
                WhatsAppMessage.created_at >= cutoff,
            ).count()

            data = {
                "period_days": days,
                "total_messages": total,
                "sent": sent,
                "received": received,
            }

            msg = (
                f"📊 **Analytics Summary (Last {days} days)**\n"
                f"• Total Messages: **{total}**\n"
                f"• Sent: **{sent}**\n"
                f"• Received: **{received}**"
            )
            return {"status": "success", "message": msg, "data": data, "ui_action": "show_card"}

        except Exception as exc:
            logger.warning("Analytics query failed: %s", exc)
            return {
                "status": "success",
                "message": f"📊 View detailed analytics on the **Tracking** page.",
                "navigate_to": "/dashboard/tracking",
                "ui_action": "navigate",
            }


@action_registry.register(domain="analytics", action="trends")
class AnalyticsTrendsAction(BaseAction):
    name = "analytics_trends"
    description = "Show message trends over time"
    required_params: List[str] = []
    optional_params = ["days"]

    def execute(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "success",
            "message": "📈 View message trends on the **Tracking** page for detailed charts and breakdowns.",
            "navigate_to": "/dashboard/tracking",
            "ui_action": "navigate",
        }


@action_registry.register(domain="analytics", action="export")
class AnalyticsExportAction(BaseAction):
    name = "analytics_export"
    description = "Export analytics data"
    required_params: List[str] = []
    optional_params = ["format", "date_range"]

    def execute(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "success",
            "message": "📥 To export analytics, go to the **Tracking** page and use the export button.",
            "navigate_to": "/dashboard/tracking",
            "ui_action": "navigate",
        }
