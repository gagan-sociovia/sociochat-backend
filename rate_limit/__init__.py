"""
Rate Limit Stubs for SocioChat.
Provides a no-op rate limit decorator.
"""

from functools import wraps


def rate_limit(*args, **kwargs):
    """No-op rate limit decorator — allows all requests through."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*a, **kw):
            return f(*a, **kw)
        return decorated_function

    # Support both @rate_limit and @rate_limit(...)
    if len(args) == 1 and callable(args[0]):
        return decorator(args[0])
    return decorator
