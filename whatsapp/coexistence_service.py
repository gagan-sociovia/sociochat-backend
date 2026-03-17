"""
WhatsApp Coexistence Service
=============================

Handles WhatsApp Business API Coexistence mode features:
- Token bucket rate limiting (5 MPS for coexistence, 80+ for standard)
- Echo message handling (messages from mobile WhatsApp Business App)
- History sync processing (180 days of chat history after QR handshake)
- Device activity monitoring (alert if >10 days inactive)
- Contact management (CRM-style tracking)

Coexistence mode allows businesses to keep their existing WhatsApp Business
App running on their phone while also using the Cloud API for automation.

Key constraints:
- 5 messages per second (MPS) rate limit
- All messages sent from mobile app appear as "echo" messages
- History sync sends up to 180 days of existing chat data
- Phone must remain active; >10 days inactive triggers alert
"""

import os
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, Optional, Tuple, List

from models import db
from .models import (
    WhatsAppAccount,
    WhatsAppMessage,
    WhatsAppConversation,
    WhatsAppRateLimit,
    WhatsAppHistorySyncLog,
    WhatsAppContact,
)

logger = logging.getLogger(__name__)


# ============================================================
# Rate Limiter (Token Bucket)
# ============================================================

class CoexistenceRateLimiter:
    """
    Token bucket rate limiter for WhatsApp messaging.
    
    Coexistence mode: 5 MPS (messages per second)
    Standard Cloud API: 80-1000 MPS (configurable)
    
    Uses database-backed token bucket for persistence.
    Falls back to in-memory if DB unavailable.
    """
    
    # In-memory fallback
    _memory_buckets: Dict[str, Dict] = {}
    
    @staticmethod
    def get_bucket(phone_number_id: str) -> WhatsAppRateLimit:
        """Get or create a rate limit bucket for a phone number."""
        bucket = WhatsAppRateLimit.query.get(phone_number_id)
        if not bucket:
            # Determine MPS from account settings
            account = WhatsAppAccount.query.filter_by(
                phone_number_id=phone_number_id
            ).first()
            
            max_tokens = 5.0  # Default: coexistence limit
            if account:
                max_tokens = float(account.mps_limit or 5)
                if not account.is_coexistence:
                    max_tokens = float(account.mps_limit or 80)
            
            bucket = WhatsAppRateLimit(
                phone_number_id=phone_number_id,
                tokens=max_tokens,
                max_tokens=max_tokens,
                last_refill=datetime.now(timezone.utc),
            )
            db.session.add(bucket)
            db.session.commit()
        
        return bucket
    
    @staticmethod
    def refill_tokens(bucket: WhatsAppRateLimit) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = datetime.now(timezone.utc)
        last_refill = bucket.last_refill
        if last_refill.tzinfo is None:
            last_refill = last_refill.replace(tzinfo=timezone.utc)
        
        elapsed = (now - last_refill).total_seconds()
        tokens_to_add = elapsed * bucket.max_tokens  # Refill at MPS rate
        bucket.tokens = min(bucket.max_tokens, bucket.tokens + tokens_to_add)
        bucket.last_refill = now
        
        # Reset daily counter if needed
        today = date.today()
        if bucket.last_reset_date != today:
            bucket.messages_sent_today = 0
            bucket.last_reset_date = today
    
    @classmethod
    def acquire(cls, phone_number_id: str, count: int = 1) -> Tuple[bool, Dict[str, Any]]:
        """
        Try to acquire tokens for sending messages.
        
        Args:
            phone_number_id: The phone number to rate limit
            count: Number of tokens (messages) to acquire
            
        Returns:
            Tuple of (allowed: bool, info: dict)
            info contains: tokens_remaining, retry_after_ms, mps_limit
        """
        try:
            bucket = cls.get_bucket(phone_number_id)
            cls.refill_tokens(bucket)
            
            if bucket.tokens >= count:
                bucket.tokens -= count
                bucket.messages_sent_today += count
                db.session.commit()
                
                return True, {
                    "tokens_remaining": bucket.tokens,
                    "mps_limit": bucket.max_tokens,
                    "messages_sent_today": bucket.messages_sent_today,
                }
            else:
                # Calculate retry delay
                tokens_needed = count - bucket.tokens
                retry_after_ms = int((tokens_needed / bucket.max_tokens) * 1000)
                db.session.commit()
                
                return False, {
                    "tokens_remaining": bucket.tokens,
                    "mps_limit": bucket.max_tokens,
                    "retry_after_ms": retry_after_ms,
                    "messages_sent_today": bucket.messages_sent_today,
                    "error": f"Rate limit exceeded. Retry after {retry_after_ms}ms. Limit: {int(bucket.max_tokens)} MPS.",
                }
        except Exception as e:
            logger.warning(f"Rate limiter DB error, falling back to allow: {e}")
            # Fail-open: allow the message if rate limiter has issues
            return True, {"tokens_remaining": -1, "fallback": True}
    
    @classmethod
    def get_status(cls, phone_number_id: str) -> Dict[str, Any]:
        """Get current rate limit status without consuming tokens."""
        try:
            bucket = cls.get_bucket(phone_number_id)
            cls.refill_tokens(bucket)
            db.session.commit()
            
            return {
                "phone_number_id": phone_number_id,
                "tokens_available": round(bucket.tokens, 2),
                "max_tokens": bucket.max_tokens,
                "mps_limit": bucket.max_tokens,
                "messages_sent_today": bucket.messages_sent_today,
                "utilization_pct": round((1 - bucket.tokens / bucket.max_tokens) * 100, 1) if bucket.max_tokens > 0 else 0,
            }
        except Exception as e:
            logger.warning(f"Rate limiter status error: {e}")
            return {"error": str(e)}


# ============================================================
# Echo Handler (Coexistence)
# ============================================================

class EchoHandler:
    """
    Handles echo messages from the WhatsApp Business mobile app.
    
    In coexistence mode, when a business sends a message from the
    mobile WhatsApp Business App, Meta sends an echo webhook event.
    These are stored with direction='echo' to distinguish them from
    messages sent via the Cloud API (direction='outgoing').
    """
    
    @staticmethod
    def process_echo(
        phone_number_id: str,
        message: Dict[str, Any],
        contact: Optional[Dict[str, Any]] = None,
    ) -> Optional[WhatsAppMessage]:
        """
        Process an echo message from the mobile app.
        
        Args:
            phone_number_id: Our business phone number ID
            message: The message object from webhook
            contact: Contact info if available
            
        Returns:
            The created WhatsAppMessage or None
        """
        from .utils import normalize_phone, parse_whatsapp_timestamp, extract_message_text, get_message_type
        
        wamid = message.get("id")
        to_phone = message.get("to")  # Recipient (customer)
        msg_type = get_message_type(message)
        timestamp = message.get("timestamp")
        
        if not wamid or not to_phone:
            logger.warning("Echo message missing wamid or 'to' field")
            return None
        
        # Check dedup
        existing = WhatsAppMessage.query.filter_by(wamid=wamid).first()
        if existing:
            logger.debug(f"Duplicate echo skipped: {wamid}")
            return None
        
        to_phone = normalize_phone(to_phone)
        
        # Get account
        account = WhatsAppAccount.query.filter_by(
            phone_number_id=phone_number_id,
            is_active=True,
        ).first()
        
        if not account:
            logger.warning(f"Echo: No active account for phone_number_id {phone_number_id}")
            return None
        
        # Update last echo activity
        now = datetime.now(timezone.utc)
        account.last_echo_at = now
        account.last_mobile_activity_at = now
        account.device_inactive_alert_sent = False  # Reset alert flag on activity
        
        # Get or create conversation
        conversation = WhatsAppConversation.query.filter_by(
            account_id=account.id,
            user_phone=to_phone,
        ).first()
        
        if not conversation:
            contact_name = contact.get("profile", {}).get("name") if contact else None
            conversation = WhatsAppConversation(
                account_id=account.id,
                user_phone=to_phone,
                user_name=contact_name,
                status="open",
            )
            db.session.add(conversation)
            db.session.flush()
        
        # Build content
        content = {"type": msg_type}
        if msg_type == "text":
            content["text"] = message.get("text", {}).get("body", "")
        elif msg_type in ("image", "video", "audio", "document", "sticker"):
            media = message.get(msg_type, {})
            content.update({
                "media_id": media.get("id"),
                "mime_type": media.get("mime_type"),
                "caption": media.get("caption"),
            })
        
        msg_timestamp = parse_whatsapp_timestamp(timestamp) or now
        
        # Create echo message record
        msg_record = WhatsAppMessage(
            conversation_id=conversation.id,
            direction="echo",  # Key: marks as sent from mobile app
            type=msg_type,
            content=content,
            wamid=wamid,
            status="sent",
            created_at=msg_timestamp,
            sent_at=msg_timestamp,
        )
        db.session.add(msg_record)
        
        # Update conversation
        conversation.last_message_at = msg_timestamp
        conversation.last_outbound_at = msg_timestamp
        conversation.status = "open"
        
        db.session.commit()
        
        logger.info(f"Stored echo message: {wamid} to {to_phone} (type={msg_type})")
        return msg_record


# ============================================================
# History Sync Handler
# ============================================================

class HistorySyncHandler:
    """
    Processes history sync data from Meta after coexistence QR handshake.
    
    After pairing, Meta sends up to 180 days of chat history in batches.
    This handler processes chunks, deduplicates, and stores messages.
    """
    
    @staticmethod
    def process_history_batch(
        account_id: int,
        messages: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a batch of history sync messages.
        
        Args:
            account_id: The WhatsApp account ID
            messages: List of message objects from history sync
            batch_id: Optional batch identifier for tracking
            
        Returns:
            Dict with processing results
        """
        from .utils import normalize_phone, parse_whatsapp_timestamp, get_message_type
        
        account = WhatsAppAccount.query.get(account_id)
        if not account:
            return {"success": False, "error": "Account not found"}
        
        # Create sync log entry
        sync_log = WhatsAppHistorySyncLog(
            account_id=account_id,
            batch_id=batch_id,
            status="processing",
            messages_received=len(messages),
        )
        db.session.add(sync_log)
        db.session.flush()
        
        stored = 0
        duplicated = 0
        errors = 0
        
        for msg_data in messages:
            try:
                wamid = msg_data.get("id")
                if not wamid:
                    errors += 1
                    continue
                
                # Dedup by wamid
                existing = WhatsAppMessage.query.filter_by(wamid=wamid).first()
                if existing:
                    duplicated += 1
                    continue
                
                # Determine direction
                from_phone = msg_data.get("from", "")
                to_phone = msg_data.get("to", "")
                
                # If from matches our business number, it's outgoing/echo
                is_outgoing = (from_phone == account.display_phone_number or
                              from_phone == account.phone_number_id)
                
                user_phone = to_phone if is_outgoing else from_phone
                user_phone = normalize_phone(user_phone)
                direction = "echo" if is_outgoing else "incoming"
                
                # Get or create conversation
                conversation = WhatsAppConversation.query.filter_by(
                    account_id=account_id,
                    user_phone=user_phone,
                ).first()
                
                if not conversation:
                    conversation = WhatsAppConversation(
                        account_id=account_id,
                        user_phone=user_phone,
                        status="open",
                    )
                    db.session.add(conversation)
                    db.session.flush()
                
                msg_type = get_message_type(msg_data)
                timestamp = msg_data.get("timestamp")
                msg_time = parse_whatsapp_timestamp(timestamp) if timestamp else datetime.now(timezone.utc)
                
                # Build content
                content = {"type": msg_type, "history_sync": True}
                if msg_type == "text":
                    content["text"] = msg_data.get("text", {}).get("body", "")
                
                msg_record = WhatsAppMessage(
                    conversation_id=conversation.id,
                    direction=direction,
                    type=msg_type,
                    content=content,
                    wamid=wamid,
                    status="delivered" if direction == "incoming" else "sent",
                    created_at=msg_time,
                )
                db.session.add(msg_record)
                stored += 1
                
                # Update conversation timestamp
                if not conversation.last_message_at or msg_time > conversation.last_message_at:
                    conversation.last_message_at = msg_time
                    
            except Exception as e:
                logger.warning(f"History sync message error: {e}")
                errors += 1
        
        # Update sync log
        sync_log.messages_stored = stored
        sync_log.messages_duplicated = duplicated
        sync_log.status = "completed" if errors == 0 else "completed_with_errors"
        sync_log.completed_at = datetime.now(timezone.utc)
        if errors > 0:
            sync_log.error_message = f"{errors} messages failed to process"
        
        # Update account sync progress
        total_synced = WhatsAppHistorySyncLog.query.filter_by(
            account_id=account_id,
        ).with_entities(
            db.func.sum(WhatsAppHistorySyncLog.messages_stored)
        ).scalar() or 0
        
        account.history_sync_progress = min(100, int((total_synced / max(1, total_synced + len(messages))) * 100))
        
        db.session.commit()
        
        logger.info(
            f"History sync batch processed: account={account_id}, "
            f"received={len(messages)}, stored={stored}, duplicated={duplicated}, errors={errors}"
        )
        
        return {
            "success": True,
            "batch_id": batch_id,
            "messages_received": len(messages),
            "messages_stored": stored,
            "messages_duplicated": duplicated,
            "errors": errors,
        }
    
    @staticmethod
    def mark_sync_complete(account_id: int) -> None:
        """Mark history sync as fully completed for an account."""
        account = WhatsAppAccount.query.get(account_id)
        if account:
            account.history_sync_completed = True
            account.history_sync_progress = 100
            account.sync_status = "synced"
            db.session.commit()
            logger.info(f"History sync completed for account {account_id}")


# ============================================================
# Device Activity Monitor
# ============================================================

class DeviceActivityMonitor:
    """
    Monitors mobile app activity for coexistence accounts.
    
    Tracks last echo message timestamp to detect when the mobile
    WhatsApp Business App hasn't been used for >10 days.
    
    If inactive for 10+ days, sends an alert to the account owner.
    """
    
    INACTIVITY_THRESHOLD_DAYS = 10
    
    @classmethod
    def check_all_accounts(cls) -> List[Dict[str, Any]]:
        """
        Check all coexistence accounts for inactivity.
        
        Returns list of accounts that need attention.
        """
        threshold = datetime.now(timezone.utc) - timedelta(days=cls.INACTIVITY_THRESHOLD_DAYS)
        
        inactive_accounts = WhatsAppAccount.query.filter(
            WhatsAppAccount.is_coexistence == True,
            WhatsAppAccount.is_active == True,
            WhatsAppAccount.device_inactive_alert_sent == False,
            db.or_(
                WhatsAppAccount.last_echo_at == None,
                WhatsAppAccount.last_echo_at < threshold,
            ),
        ).all()
        
        alerts = []
        for account in inactive_accounts:
            last_activity = account.last_echo_at or account.coexistence_paired_at or account.created_at
            days_inactive = (datetime.now(timezone.utc) - last_activity).days if last_activity else 999
            
            alerts.append({
                "account_id": account.id,
                "phone_number_id": account.phone_number_id,
                "display_phone_number": account.display_phone_number,
                "workspace_id": account.workspace_id,
                "days_inactive": days_inactive,
                "last_activity": last_activity.isoformat() if last_activity else None,
            })
        
        return alerts
    
    @classmethod
    def mark_alert_sent(cls, account_id: int) -> None:
        """Mark that an inactivity alert was sent for this account."""
        account = WhatsAppAccount.query.get(account_id)
        if account:
            account.device_inactive_alert_sent = True
            db.session.commit()
    
    @classmethod
    def get_device_status(cls, account_id: int) -> Dict[str, Any]:
        """Get device activity status for an account."""
        account = WhatsAppAccount.query.get(account_id)
        if not account:
            return {"error": "Account not found"}
        
        if not account.is_coexistence:
            return {
                "account_id": account_id,
                "is_coexistence": False,
                "status": "standard",
                "message": "Standard Cloud API account - no mobile dependency",
            }
        
        last_activity = account.last_echo_at or account.last_mobile_activity_at
        now = datetime.now(timezone.utc)
        
        if last_activity:
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            days_since = (now - last_activity).days
        else:
            days_since = None
        
        if days_since is None:
            status = "unknown"
            health = "warning"
        elif days_since <= 2:
            status = "active"
            health = "healthy"
        elif days_since <= 7:
            status = "idle"
            health = "warning"
        elif days_since <= cls.INACTIVITY_THRESHOLD_DAYS:
            status = "at_risk"
            health = "critical"
        else:
            status = "inactive"
            health = "danger"
        
        return {
            "account_id": account_id,
            "is_coexistence": True,
            "status": status,
            "health": health,
            "last_mobile_activity": last_activity.isoformat() if last_activity else None,
            "days_since_activity": days_since,
            "threshold_days": cls.INACTIVITY_THRESHOLD_DAYS,
            "alert_sent": account.device_inactive_alert_sent,
            "paired_at": account.coexistence_paired_at.isoformat() if account.coexistence_paired_at else None,
        }


# ============================================================
# Contact Manager
# ============================================================

class ContactManager:
    """
    CRM-style contact management for WhatsApp conversations.
    
    Automatically creates/updates contacts when messages are received
    and provides search/label/filter functionality.
    """
    
    @staticmethod
    def upsert_contact(
        account_id: int,
        phone: str,
        name: Optional[str] = None,
        wa_id: Optional[str] = None,
    ) -> WhatsAppContact:
        """Create or update a contact."""
        from .utils import normalize_phone
        phone = normalize_phone(phone)
        wa_id = wa_id or phone
        
        contact = WhatsAppContact.query.filter_by(
            account_id=account_id,
            wa_id=wa_id,
        ).first()
        
        if contact:
            if name and not contact.name:
                contact.name = name
            contact.last_message_at = datetime.now(timezone.utc)
            contact.total_messages += 1
        else:
            contact = WhatsAppContact(
                account_id=account_id,
                phone=phone,
                name=name,
                wa_id=wa_id,
                last_message_at=datetime.now(timezone.utc),
                total_messages=1,
            )
            db.session.add(contact)
        
        return contact
    
    @staticmethod
    def add_label(contact_id: int, label: str) -> bool:
        """Add a label to a contact."""
        contact = WhatsAppContact.query.get(contact_id)
        if not contact:
            return False
        labels = contact.labels or []
        if label not in labels:
            labels.append(label)
            contact.labels = labels
            db.session.commit()
        return True
    
    @staticmethod
    def remove_label(contact_id: int, label: str) -> bool:
        """Remove a label from a contact."""
        contact = WhatsAppContact.query.get(contact_id)
        if not contact:
            return False
        labels = contact.labels or []
        if label in labels:
            labels.remove(label)
            contact.labels = labels
            db.session.commit()
        return True
    
    @staticmethod
    def search_contacts(
        account_id: int,
        query: Optional[str] = None,
        labels: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[WhatsAppContact], int]:
        """Search contacts by name/phone or filter by labels."""
        q = WhatsAppContact.query.filter_by(account_id=account_id)
        
        if query:
            search = f"%{query}%"
            q = q.filter(
                db.or_(
                    WhatsAppContact.name.ilike(search),
                    WhatsAppContact.phone.ilike(search),
                )
            )
        
        # Label filtering would need JSON contains which varies by DB
        # For PostgreSQL, use JSONB contains
        if labels:
            for label in labels:
                q = q.filter(WhatsAppContact.labels.contains([label]))
        
        total = q.count()
        contacts = q.order_by(
            WhatsAppContact.last_message_at.desc().nullslast()
        ).offset(offset).limit(limit).all()
        
        return contacts, total


# ============================================================
# Meta Error Handler
# ============================================================

META_ERROR_CODES = {
    "130429": "Rate limit hit — too many messages per second",
    "131056": "Pair rate limit — too many pairing attempts",
    "131000": "Invalid access token — needs re-authentication",
    "131031": "Account has been locked",
    "131051": "Message type not supported in coexistence",
    "131047": "Re-engagement message outside 24h window",
    "131026": "Recipient not a WA user",
    "132000": "Template parameters mismatch",
}


def classify_meta_error(error_code: str) -> Dict[str, Any]:
    """
    Classify a Meta error code and return actionable information.
    
    Args:
        error_code: The error code from Meta webhook or API response
        
    Returns:
        Dict with classification, description, and recommended action
    """
    code_str = str(error_code)
    description = META_ERROR_CODES.get(code_str, f"Unknown error code: {code_str}")
    
    if code_str == "130429":
        return {
            "code": code_str,
            "description": description,
            "severity": "warning",
            "action": "throttle",
            "retry": True,
            "retry_after_ms": 1000,
        }
    elif code_str == "131056":
        return {
            "code": code_str,
            "description": description,
            "severity": "warning",
            "action": "wait",
            "retry": True,
            "retry_after_ms": 60000,
        }
    elif code_str == "131000":
        return {
            "code": code_str,
            "description": description,
            "severity": "critical",
            "action": "re_authenticate",
            "retry": False,
        }
    else:
        return {
            "code": code_str,
            "description": description,
            "severity": "error",
            "action": "investigate",
            "retry": False,
        }
