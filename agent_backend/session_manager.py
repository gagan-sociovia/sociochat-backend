"""
Session Manager — multi-turn conversation state.
=================================================

Keeps track of:
- Current action flow (e.g., halfway through creating a drip campaign)
- Collected params so far
- Conversation history (last 10 messages for context)
- Per-user session with TTL (30 min)

Uses in-memory dict with periodic cleanup — no extra DB needed.
"""

import time
import uuid
import threading
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 30 * 60  # 30 minutes
MAX_HISTORY = 10               # Keep last N message pairs
CLEANUP_INTERVAL = 300         # Run cleanup every 5 min


@dataclass
class AgentSession:
    """One user's conversation session."""
    session_id: str
    workspace_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    # Multi-turn state
    current_domain: Optional[str] = None
    current_action: Optional[str] = None
    collected_params: Dict[str, Any] = field(default_factory=dict)
    missing_params: List[str] = field(default_factory=list)
    awaiting_confirmation: bool = False

    # Conversation history  [{role: "user"/"agent", text: "..."}]
    history: List[Dict[str, str]] = field(default_factory=list)

    def touch(self):
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

    def add_message(self, role: str, text: str):
        self.history.append({"role": role, "text": text})
        # Trim to keep last MAX_HISTORY pairs (2 * MAX_HISTORY entries)
        max_entries = MAX_HISTORY * 2
        if len(self.history) > max_entries:
            self.history = self.history[-max_entries:]

    def clear_action_state(self):
        """Reset multi-turn action state (after completion or cancel)."""
        self.current_domain = None
        self.current_action = None
        self.collected_params = {}
        self.missing_params = []
        self.awaiting_confirmation = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "current_domain": self.current_domain,
            "current_action": self.current_action,
            "collected_params": self.collected_params,
            "missing_params": self.missing_params,
            "awaiting_confirmation": self.awaiting_confirmation,
            "history_length": len(self.history),
        }


class SessionManager:
    """In-memory session store with TTL and background cleanup."""

    _instance: Optional["SessionManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions = {}
            cls._instance._lock = threading.Lock()
            cls._instance._start_cleanup_thread()
        return cls._instance

    _sessions: Dict[str, AgentSession]
    _lock: threading.Lock

    # ---- Public API --------------------------------------------------------

    def get_or_create(self, session_id: Optional[str], workspace_id: str) -> AgentSession:
        """Return existing session (if valid) or create a new one."""
        with self._lock:
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                if not s.is_expired():
                    s.touch()
                    return s
                else:
                    del self._sessions[session_id]

            # Create new
            new_id = session_id or str(uuid.uuid4())
            session = AgentSession(session_id=new_id, workspace_id=workspace_id)
            self._sessions[new_id] = session
            return session

    def get(self, session_id: str) -> Optional[AgentSession]:
        with self._lock:
            s = self._sessions.get(session_id)
            if s and not s.is_expired():
                s.touch()
                return s
            return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    # ---- Cleanup -----------------------------------------------------------

    def _cleanup_expired(self):
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
            for sid in expired:
                del self._sessions[sid]
            if expired:
                logger.debug("Cleaned up %d expired agent sessions", len(expired))

    def _start_cleanup_thread(self):
        def _run():
            while True:
                time.sleep(CLEANUP_INTERVAL)
                try:
                    self._cleanup_expired()
                except Exception as exc:
                    logger.warning("Session cleanup error: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="agent-session-cleanup")
        t.start()


# Module-level singleton
session_manager = SessionManager()
