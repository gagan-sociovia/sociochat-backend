"""
Agent Backend for SocioChat.
=============================

Agentic AI layer — natural language → action execution.

Provides:
- ActionRegistry + BaseAction plugin system
- IntentParser (Gemini NLU)
- ActionExecutor pipeline
- SessionManager for multi-turn conversations
- Agent API blueprint (/api/agent/*)
- Stub decorators for backward compat
"""
from functools import wraps
from flask import request, jsonify


def require_admin_only(f):
    """Stub decorator — allows all requests through (no admin check)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# Import core modules to wire everything up
from .action_registry import action_registry, BaseAction  # noqa: E402
from .session_manager import session_manager               # noqa: E402
from .intent_parser import intent_parser                    # noqa: E402
from .action_executor import action_executor                # noqa: E402
from .agent_routes import agent_bp                          # noqa: E402

# Import action modules so they auto-register
from . import actions  # noqa: E402, F401

__all__ = [
    "require_admin_only",
    "action_registry",
    "BaseAction",
    "session_manager",
    "intent_parser",
    "action_executor",
    "agent_bp",
]
