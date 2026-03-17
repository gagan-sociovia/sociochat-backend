"""
Subscription Service Stubs for SocioChat.
In the standalone WhatsApp product, there are no subscription limits.
All limits are unlimited by default.
"""


def check_message_limit(workspace_id, *args, **kwargs):
    """Always allow — no subscription limits in standalone mode.
    Returns (allowed, current_usage, limit) tuple matching _enforce_message_limit unpacking.
    """
    return True, 0, 999999


def record_message_sent(workspace_id, *args, **kwargs):
    """No-op — no subscription tracking in standalone mode."""
    pass


def check_flow_limit(workspace_id, *args, **kwargs):
    """Always allow — no subscription limits in standalone mode."""
    return {"allowed": True, "remaining": 999999}
