"""
WhatsApp Automation Models
==========================

Database models for automation rules in a multi-tenant SaaS environment.

Design Principles:
- All rules are scoped to workspace_id AND account_id
- Soft delete via is_active flag
- Audit trail via created_at, updated_at
- JSON fields for flexible configuration
- Rate limiting built-in to prevent spam

Rule Types:
- WELCOME: First message from a new contact
- AWAY: Message received outside business hours
- KEYWORD: Specific word/phrase triggers
- COMMAND: Slash commands like /help, /menu
- FAQ: FAQ knowledge base matching
- AI_CHAT: AI-powered conversational response
"""

import enum
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from models import db
from sqlalchemy import Index, UniqueConstraint, JSON, Text


# ============================================================
# Enums
# ============================================================

class AutomationRuleType(enum.Enum):
    """Types of automation rules."""
    WELCOME = "welcome"          # First message from new contact
    AWAY = "away"                # Outside business hours
    KEYWORD = "keyword"          # Keyword/phrase trigger
    COMMAND = "command"          # Slash command trigger
    FAQ = "faq"                  # FAQ knowledge base matching
    AI_CHAT = "ai_chat"          # AI-powered response (Gemini)
    DEFAULT = "default"          # Fallback when no other rule matches


class AutomationResponseType(enum.Enum):
    """Types of automated responses."""
    TEXT = "text"                # Plain text message
    TEMPLATE = "template"        # WhatsApp template
    INTERACTIVE = "interactive"  # Buttons or list
    FLOW = "flow"               # WhatsApp Flow
    FAQ = "faq"                 # FAQ answer
    AI = "ai"                   # AI-generated response


class AutomationStatus(enum.Enum):
    """Automation rule status."""
    ACTIVE = "active"
    PAUSED = "paused"
    DRAFT = "draft"


# ============================================================
# TABLE: Automation Rules
# ============================================================

class WhatsAppAutomationRule(db.Model):
    """
    Automation rules for auto-replies and triggers.
    
    Multi-tenant: Scoped by workspace_id and account_id.
    
    Examples:
    - Welcome message when new user messages for first time
    - Away message outside business hours
    - Keyword trigger: "pricing" → send price list
    - Command trigger: "/help" → send help menu
    """
    __tablename__ = "whatsapp_automation_rules"
    __table_args__ = (
        Index("ix_automation_workspace", "workspace_id"),
        Index("ix_automation_account", "account_id"),
        Index("ix_automation_type", "rule_type"),
        Index("ix_automation_active", "is_active"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant scoping (REQUIRED)
    workspace_id = db.Column(db.String(255), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("whatsapp_accounts.id"), nullable=False)
    
    # Rule identification
    name = db.Column(db.String(255), nullable=False)  # Human-readable name
    description = db.Column(db.Text, nullable=True)   # Optional description
    
    # Rule type and status
    rule_type = db.Column(db.String(32), nullable=False, default="keyword")  # welcome, away, keyword, command
    status = db.Column(db.String(32), nullable=False, default="active")  # active, paused, draft
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Trigger configuration (JSON for flexibility)
    # For KEYWORD: {"keywords": ["hi", "hello"], "match_type": "contains|exact|starts_with"}
    # For COMMAND: {"command": "/help", "aliases": ["/h"]}
    # For WELCOME: {} (triggers on first message)
    # For AWAY: {"schedule": {"mon": {"start": "09:00", "end": "18:00"}, ...}}
    trigger_config = db.Column(JSON, nullable=False, default=dict)
    
    # Response configuration (JSON for flexibility)
    # For TEXT: {"message": "Hello! How can I help?"}
    # For TEMPLATE: {"template_name": "welcome", "language": "en_US", "components": [...]}
    # For INTERACTIVE: {"type": "buttons", "body": "...", "buttons": [...]}
    response_type = db.Column(db.String(32), nullable=False, default="text")  # text, template, interactive
    response_config = db.Column(JSON, nullable=False, default=dict)
    
    # Rate limiting (prevent spam)
    cooldown_seconds = db.Column(db.Integer, default=0)  # Minimum seconds between triggers for same user
    max_triggers_per_day = db.Column(db.Integer, default=0)  # 0 = unlimited
    
    # Priority (lower = higher priority, for rule ordering)
    priority = db.Column(db.Integer, default=100)
    
    # Statistics
    trigger_count = db.Column(db.Integer, default=0)  # Total times triggered
    last_triggered_at = db.Column(db.DateTime, nullable=True)
    
    # Audit
    created_by = db.Column(db.String(255), nullable=True)  # User who created
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationship
    account = db.relationship("WhatsAppAccount", backref=db.backref("automation_rules", lazy="dynamic"))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "account_id": self.account_id,
            "name": self.name,
            "description": self.description,
            "rule_type": self.rule_type,
            "status": self.status,
            "is_active": self.is_active,
            "trigger_config": self.trigger_config,
            "response_type": self.response_type,
            "response_config": self.response_config,
            "cooldown_seconds": self.cooldown_seconds,
            "max_triggers_per_day": self.max_triggers_per_day,
            "priority": self.priority,
            "trigger_count": self.trigger_count,
            "last_triggered_at": self.last_triggered_at.isoformat() + "Z" if self.last_triggered_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


# ============================================================
# TABLE: Automation Logs
# ============================================================

class WhatsAppAutomationLog(db.Model):
    """
    Log of automation rule executions.
    
    Used for:
    - Debugging automation issues
    - Analytics on automation performance
    - Rate limiting enforcement
    - Audit trail
    """
    __tablename__ = "whatsapp_automation_logs"
    __table_args__ = (
        Index("ix_automation_log_workspace", "workspace_id"),
        Index("ix_automation_log_rule", "rule_id"),
        Index("ix_automation_log_conversation", "conversation_id"),
        Index("ix_automation_log_created", "created_at"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant scoping
    workspace_id = db.Column(db.String(255), nullable=False, index=True)
    
    # What triggered
    rule_id = db.Column(db.Integer, db.ForeignKey("whatsapp_automation_rules.id"), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey("whatsapp_conversations.id"), nullable=False)
    
    # Trigger context
    trigger_message_id = db.Column(db.Integer, nullable=True)  # Message that triggered
    trigger_text = db.Column(db.Text, nullable=True)           # Text that matched (for debugging)
    matched_keyword = db.Column(db.String(255), nullable=True) # Which keyword/command matched
    
    # Response sent
    response_message_id = db.Column(db.Integer, nullable=True)  # Message sent in response
    response_success = db.Column(db.Boolean, default=True)
    response_error = db.Column(db.Text, nullable=True)
    
    # Timing
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    response_time_ms = db.Column(db.Integer, nullable=True)  # How long to respond
    
    # Relationships
    rule = db.relationship("WhatsAppAutomationRule", backref=db.backref("logs", lazy="dynamic"))
    conversation = db.relationship("WhatsAppConversation", backref=db.backref("automation_logs", lazy="dynamic"))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "rule_id": self.rule_id,
            "conversation_id": self.conversation_id,
            "trigger_message_id": self.trigger_message_id,
            "trigger_text": self.trigger_text,
            "matched_keyword": self.matched_keyword,
            "response_message_id": self.response_message_id,
            "response_success": self.response_success,
            "response_error": self.response_error,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "response_time_ms": self.response_time_ms,
        }


# ============================================================
# TABLE: Business Hours (for Away messages)
# ============================================================

class WhatsAppBusinessHours(db.Model):
    """
    Business hours configuration for away message automation.
    
    Stored separately for cleaner management.
    """
    __tablename__ = "whatsapp_business_hours"
    __table_args__ = (
        UniqueConstraint("workspace_id", "account_id", name="uq_business_hours"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant scoping
    workspace_id = db.Column(db.String(255), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("whatsapp_accounts.id"), nullable=False)
    
    # Timezone for schedule interpretation
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    
    # Schedule as JSON
    # Format: {"mon": {"enabled": true, "start": "09:00", "end": "18:00"}, ...}
    # Days: mon, tue, wed, thu, fri, sat, sun
    schedule = db.Column(JSON, nullable=False, default=dict)
    
    # Is business hours checking enabled?
    is_enabled = db.Column(db.Boolean, default=False, nullable=False)
    
    # Audit
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationship
    account = db.relationship("WhatsAppAccount", backref=db.backref("business_hours", uselist=False))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "account_id": self.account_id,
            "timezone": self.timezone,
            "schedule": self.schedule,
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }
    
    def is_within_business_hours(self, check_time: Optional[datetime] = None) -> bool:
        """
        Check if given time is within business hours.
        
        Args:
            check_time: Time to check (defaults to now in configured timezone)
            
        Returns:
            True if within business hours, False otherwise
        """
        if not self.is_enabled:
            return True  # If not enabled, always "within hours"
        
        import pytz
        from datetime import time
        
        try:
            tz = pytz.timezone(self.timezone)
        except:
            tz = pytz.UTC
        
        if check_time is None:
            check_time = datetime.now(tz)
        else:
            check_time = check_time.astimezone(tz)
        
        # Get day of week (mon=0, sun=6)
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        day_name = day_names[check_time.weekday()]
        
        day_schedule = self.schedule.get(day_name, {})
        
        if not day_schedule.get("enabled", False):
            return False
        
        try:
            start_str = day_schedule.get("start", "00:00")
            end_str = day_schedule.get("end", "23:59")
            
            start_parts = start_str.split(":")
            end_parts = end_str.split(":")
            
            start_time = time(int(start_parts[0]), int(start_parts[1]))
            end_time = time(int(end_parts[0]), int(end_parts[1]))
            
            current_time = check_time.time()
            
            return start_time <= current_time <= end_time
        except:
            return True  # On error, assume within hours


# ============================================================
# TABLE: Contact Automation Overrides (Per-Contact Toggle)
# ============================================================

class ContactAutomationOverride(db.Model):
    """
    Per-contact overrides for automation rules.
    
    Allows users to disable specific automation types for individual contacts.
    Only stores disabled overrides (not enabled - that's the default).
    
    Design:
    - Uses conversation_id as FK (stable, workspace-scoped)
    - Only stores disabled overrides to minimize DB size
    - Unique constraint prevents duplicate rows
    """
    __tablename__ = "contact_automation_overrides"
    __table_args__ = (
        UniqueConstraint("workspace_id", "conversation_id", "rule_type", name="uq_contact_override"),
        Index("ix_contact_override_lookup", "workspace_id", "conversation_id"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    
    # Multi-tenant scoping
    workspace_id = db.Column(db.String(255), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("whatsapp_accounts.id"), nullable=False)
    
    # Contact identification (use conversation_id as stable FK)
    conversation_id = db.Column(db.Integer, db.ForeignKey("whatsapp_conversations.id"), nullable=False)
    
    # Which automation type is disabled
    # Types: welcome, away, command, keyword, ai_chat, faq
    rule_type = db.Column(db.String(32), nullable=False)
    
    # Is this automation type enabled for this contact?
    # Note: We only store disabled=False entries. If no entry exists, it's enabled (default).
    is_enabled = db.Column(db.Boolean, default=False, nullable=False)
    
    # Audit
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), 
                          onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    account = db.relationship("WhatsAppAccount", backref=db.backref("contact_overrides", lazy="dynamic"))
    conversation = db.relationship("WhatsAppConversation", backref=db.backref("automation_overrides", lazy="dynamic"))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "account_id": self.account_id,
            "conversation_id": self.conversation_id,
            "rule_type": self.rule_type,
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


def is_automation_disabled_for_contact(workspace_id: str, conversation_id: int, rule_type: str) -> bool:
    """
    Check if a specific automation type is disabled for a contact.
    
    Returns:
        True if disabled (override exists with is_enabled=False)
        False if enabled (no override or override with is_enabled=True)
    """
    override = ContactAutomationOverride.query.filter_by(
        workspace_id=str(workspace_id),
        conversation_id=conversation_id,
        rule_type=rule_type,
        is_enabled=False
    ).first()
    
    return override is not None


def get_contact_overrides(workspace_id: str, conversation_id: int) -> Dict[str, bool]:
    """
    Get all automation override settings for a contact.
    
    Returns:
        Dict of {rule_type: is_enabled} for all types.
        Default is True (enabled) if no override exists.
    """
    # All rule types
    all_types = ["welcome", "away", "command", "keyword", "faq", "ai_chat", "interactive_flows"]
    
    # Start with all enabled
    result = {t: True for t in all_types}
    
    # Get disabled overrides
    overrides = ContactAutomationOverride.query.filter_by(
        workspace_id=str(workspace_id),
        conversation_id=conversation_id
    ).all()
    
    for o in overrides:
        result[o.rule_type] = o.is_enabled
    
    return result


def set_contact_override(
    workspace_id: str, 
    account_id: int, 
    conversation_id: int, 
    rule_type: str, 
    is_enabled: bool
) -> bool:
    """
    Set automation override for a contact.
    
    If enabling (is_enabled=True), delete the override row (return to default).
    If disabling (is_enabled=False), create/update the override row.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        existing = ContactAutomationOverride.query.filter_by(
            workspace_id=str(workspace_id),
            conversation_id=conversation_id,
            rule_type=rule_type
        ).first()
        
        if is_enabled:
            # Enabling = delete override (return to default)
            if existing:
                db.session.delete(existing)
                db.session.commit()
        else:
            # Disabling = create/update override
            if existing:
                existing.is_enabled = False
                existing.updated_at = datetime.now(timezone.utc)
            else:
                override = ContactAutomationOverride(
                    workspace_id=str(workspace_id),
                    account_id=account_id,
                    conversation_id=conversation_id,
                    rule_type=rule_type,
                    is_enabled=False
                )
                db.session.add(override)
            db.session.commit()
        
        return True
    except Exception as e:
        db.session.rollback()
        return False
