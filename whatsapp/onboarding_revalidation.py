"""
Background revalidation for WhatsApp accounts in pending onboarding states.
Meta asset propagation can take time — promote to ACTIVE when checks pass.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Tuple

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
REVALIDATION_INTERVAL_SECONDS = int(os.getenv("WA_ONBOARDING_REVALIDATION_SECONDS", "120"))
RECONNECT_AFTER_CYCLES = int(os.getenv("WA_ONBOARDING_RECONNECT_AFTER_CYCLES", "10"))

# In-memory streak tracker: account_id -> (last_status, consecutive_unchanged_cycles)
_revalidation_streak: Dict[int, Tuple[str, int]] = {}


def _should_escalate_to_reconnect(status: str, streak: int) -> bool:
    """Escalate after repeated failed revalidation cycles."""
    if streak < RECONNECT_AFTER_CYCLES:
        return False
    if status == "FAILED":
        return True
    return status in {
        "PENDING",
        "ASSET_SHARING_PENDING",
        "WABA_NOT_VISIBLE",
        "PHONE_NOT_VISIBLE",
        "SUBSCRIPTION_PENDING",
    }


def _maybe_escalate_to_reconnect(account, validation) -> None:
    """Set RECONNECT_REQUIRED when validation repeatedly fails to progress."""
    from models import db
    from .onboarding_status import OnboardingStatus

    account_id = account.id
    current_status = validation.status
    prev = _revalidation_streak.get(account_id)

    if prev and prev[0] == current_status:
        streak = prev[1] + 1
    else:
        streak = 1

    _revalidation_streak[account_id] = (current_status, streak)

    if current_status == OnboardingStatus.ACTIVE:
        _revalidation_streak.pop(account_id, None)
        return

    if not _should_escalate_to_reconnect(current_status, streak):
        return

    account.onboarding_status = OnboardingStatus.RECONNECT_REQUIRED
    account.onboarding_error = (
        validation.onboarding_error
        or f"Setup did not complete after {streak} validation attempts"
    )
    account.is_active = False
    account.last_validation_at = datetime.now(timezone.utc)
    db.session.commit()
    _revalidation_streak.pop(account_id, None)
    logger.warning(
        "Escalated account %s to RECONNECT_REQUIRED after %s unchanged cycles (was %s)",
        account_id,
        streak,
        current_status,
    )


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

        failed_accounts = WhatsAppAccount.query.filter_by(
            onboarding_status=OnboardingStatus.FAILED
        ).limit(20).all()

        seen_ids = {a.id for a in accounts}
        for acct in failed_accounts:
            if acct.id not in seen_ids:
                accounts.append(acct)
                seen_ids.add(acct.id)

        if not accounts:
            return

        promoted = 0
        for account in accounts:
            try:
                before = account.onboarding_status
                validation = revalidate_account(account, persist=True)
                if validation.status == OnboardingStatus.ACTIVE and before != OnboardingStatus.ACTIVE:
                    promoted += 1
                    _revalidation_streak.pop(account.id, None)
                    logger.info(
                        "Promoted account %s to ACTIVE (was %s)",
                        account.id,
                        before,
                    )
                elif validation.status != OnboardingStatus.ACTIVE:
                    _maybe_escalate_to_reconnect(account, validation)
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
        "WhatsApp onboarding revalidation scheduled every %ss (reconnect after %s cycles)",
        REVALIDATION_INTERVAL_SECONDS,
        RECONNECT_AFTER_CYCLES,
    )
