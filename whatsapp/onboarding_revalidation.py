"""
Background revalidation for WhatsApp accounts in pending onboarding states.
Meta asset propagation can take time — promote to ACTIVE when checks pass.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
REVALIDATION_INTERVAL_SECONDS = int(os.getenv("WA_ONBOARDING_REVALIDATION_SECONDS", "120"))


def revalidate_pending_accounts(app) -> None:
    """Re-run validation for accounts stuck in pending onboarding states."""
    with app.app_context():
        from models import db
        from .models import WhatsAppAccount
        from .onboarding_status import REVALIDATION_STATUSES, OnboardingStatus
        from .onboarding_service import revalidate_account

        accounts = WhatsAppAccount.query.filter(
            WhatsAppAccount.onboarding_status.in_(list(REVALIDATION_STATUSES))
        ).limit(50).all()

        if not accounts:
            return

        promoted = 0
        for account in accounts:
            try:
                before = account.onboarding_status
                validation = revalidate_account(account, persist=True)
                if validation.status == OnboardingStatus.ACTIVE and before != OnboardingStatus.ACTIVE:
                    promoted += 1
                    logger.info(
                        "Promoted account %s to ACTIVE (was %s)",
                        account.id,
                        before,
                    )
            except Exception as exc:
                logger.exception("Revalidation failed for account %s: %s", account.id, exc)
                db.session.rollback()

        logger.info(
            "Onboarding revalidation: checked %s account(s), promoted %s at %s",
            len(accounts),
            promoted,
            datetime.now(timezone.utc).isoformat(),
        )


def init_onboarding_revalidation(app) -> None:
    """Start periodic revalidation job (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        lambda: revalidate_pending_accounts(app),
        "interval",
        seconds=REVALIDATION_INTERVAL_SECONDS,
        id="whatsapp_onboarding_revalidation",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(
        "WhatsApp onboarding revalidation scheduled every %ss",
        REVALIDATION_INTERVAL_SECONDS,
    )
