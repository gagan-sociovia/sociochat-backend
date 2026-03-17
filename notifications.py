"""
Notification manager for SocioChat.

Supports two modes:
  1. PostgreSQL LISTEN/NOTIFY (production) — zero-polling SSE.
  2. In-memory queue (SQLite/dev) — simple thread-safe fan-out.

Auto-detects based on the database URI.
"""

import json
import logging
import os
import queue
import random
import threading
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_engine = None
_db_dsn: str = ""
_use_pg = False

CHANNEL = "realtime_ch"


def init_notification_engine(engine, db_uri: Optional[str] = None):
    """Bootstrap notification system. Call once after db.init_app()."""
    global _engine, _db_dsn, _use_pg
    _engine = engine
    _db_dsn = db_uri or os.getenv("SQLALCHEMY_DATABASE_URI", "")
    _use_pg = _db_dsn.startswith("postgresql")

    if _use_pg:
        try:
            import psycopg2
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS realtime_events (
                        id          BIGSERIAL       PRIMARY KEY,
                        event_type  VARCHAR(64)     NOT NULL,
                        payload     TEXT            NOT NULL,
                        workspace_id VARCHAR(64),
                        created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
                    )
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_rte_id_ws
                    ON realtime_events (id, workspace_id)
                """))
                conn.commit()
            logger.info("[notifications] PostgreSQL LISTEN/NOTIFY mode ready")
        except Exception as e:
            logger.error("[notifications] Failed PG init, falling back to memory: %s", e)
            _use_pg = False
    else:
        logger.info("[notifications] In-memory notification mode (SQLite/dev)")


class NotificationManager:
    """Event fan-out — dispatches to PG or in-memory depending on init."""

    def __init__(self):
        self._subscribers: List[queue.Queue] = []
        self._lock = threading.Lock()

    # ---- broadcast ----
    def broadcast(self, event_type: str, data: dict) -> None:
        if _use_pg and _engine:
            self._broadcast_pg(event_type, data)
        else:
            self._broadcast_memory(event_type, data)

    def _broadcast_pg(self, event_type, data):
        from sqlalchemy import text
        workspace_id = data.get("workspace_id") if isinstance(data, dict) else None
        try:
            with _engine.connect() as conn:
                conn.execute(
                    text("INSERT INTO realtime_events (event_type, payload, workspace_id) VALUES (:t, :p, :w)"),
                    {"t": event_type, "p": json.dumps(data, default=str),
                     "w": str(workspace_id) if workspace_id else None},
                )
                conn.execute(text(f"NOTIFY {CHANNEL}"))
                conn.commit()
        except Exception as e:
            logger.error("[notifications] broadcast failed: %s", e)

    def _broadcast_memory(self, event_type, data):
        msg = {"type": event_type, "data": data}
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    # ---- subscribe / unsubscribe (in-memory) ----
    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # ---- PG helpers ----
    def poll(self, since_id, workspace_id=None, limit=50):
        if not _use_pg or _engine is None:
            return []
        from sqlalchemy import text
        try:
            with _engine.connect() as conn:
                if workspace_id:
                    rows = conn.execute(
                        text("SELECT id, event_type, payload FROM realtime_events "
                             "WHERE id > :since AND (workspace_id = :ws OR workspace_id IS NULL) "
                             "ORDER BY id ASC LIMIT :lim"),
                        {"since": since_id, "ws": str(workspace_id), "lim": limit},
                    )
                else:
                    rows = conn.execute(
                        text("SELECT id, event_type, payload FROM realtime_events "
                             "WHERE id > :since ORDER BY id ASC LIMIT :lim"),
                        {"since": since_id, "lim": limit},
                    )
                return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.error("[notifications] poll failed: %s", e)
            return []

    def get_latest_id(self):
        if not _use_pg or _engine is None:
            return 0
        from sqlalchemy import text
        try:
            with _engine.connect() as conn:
                return conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM realtime_events")).scalar() or 0
        except Exception:
            return 0

    def listen_loop(self, workspace_id=None, heartbeat_secs=15, max_duration_secs=300):
        """Generator yielding (event_type, payload) tuples via PG LISTEN or in-memory."""
        if _use_pg:
            yield from self._listen_pg(workspace_id, heartbeat_secs, max_duration_secs)
        else:
            yield from self._listen_memory(workspace_id, heartbeat_secs, max_duration_secs)

    def _listen_pg(self, workspace_id, heartbeat_secs, max_duration_secs):
        import psycopg2, psycopg2.extensions, time as _time
        listen_conn = None
        try:
            listen_conn = psycopg2.connect(_db_dsn)
            listen_conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            cur = listen_conn.cursor()
            cur.execute(f"LISTEN {CHANNEL}")
            last_id = self.get_latest_id()
            ticks = 0
            started = _time.monotonic()
            while True:
                if _time.monotonic() - started >= max_duration_secs:
                    yield ("reconnect", {"reason": "max_duration"})
                    return
                _time.sleep(1)
                listen_conn.poll()
                if listen_conn.notifies:
                    while listen_conn.notifies:
                        listen_conn.notifies.pop(0)
                    for ev in self.poll(last_id, workspace_id):
                        last_id = ev["id"]
                        try:
                            payload = json.loads(ev["payload"])
                        except Exception:
                            payload = {}
                        yield (ev["event_type"], payload)
                    ticks = 0
                else:
                    ticks += 1
                    if ticks >= heartbeat_secs:
                        yield ("heartbeat", None)
                        ticks = 0
        except GeneratorExit:
            pass
        except Exception as e:
            logger.error("[notifications] listen error: %s", e)
        finally:
            if listen_conn:
                try:
                    listen_conn.close()
                except Exception:
                    pass

    def _listen_memory(self, workspace_id, heartbeat_secs, max_duration_secs):
        import time as _time
        q = self.subscribe()
        started = _time.monotonic()
        ticks = 0
        try:
            while True:
                if _time.monotonic() - started >= max_duration_secs:
                    yield ("reconnect", {"reason": "max_duration"})
                    return
                try:
                    msg = q.get(timeout=1)
                    yield (msg["type"], msg["data"])
                    ticks = 0
                except queue.Empty:
                    ticks += 1
                    if ticks >= heartbeat_secs:
                        yield ("heartbeat", None)
                        ticks = 0
        except GeneratorExit:
            pass
        finally:
            self.unsubscribe(q)


notification_manager = NotificationManager()
