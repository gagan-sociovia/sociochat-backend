"""
WhatsApp Rate Limiter
=====================

Token-bucket rate limiter for WhatsApp Cloud API messaging.

Coexistence mode: 5 messages per second (MPS)
Standard mode:   80-1000 MPS (configurable per account)

Uses in-memory token bucket with thread-safe implementation.
For production at scale, this should be backed by Redis.

Key format: ratelimit:{phone_number_id}
"""

import time
import threading
import logging
from typing import Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Thread-safe token bucket for rate limiting.
    
    Tokens are refilled at a fixed rate up to a maximum capacity.
    Each send operation consumes one token.
    """
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Maximum number of tokens (burst size)
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
    
    def consume(self, count: int = 1) -> bool:
        """
        Try to consume tokens.
        
        Args:
            count: Number of tokens to consume
            
        Returns:
            True if tokens were consumed, False if rate limited
        """
        with self._lock:
            self._refill()
            if self.tokens >= count:
                self.tokens -= count
                return True
            return False
    
    def available(self) -> int:
        """Get currently available tokens."""
        with self._lock:
            self._refill()
            return int(self.tokens)
    
    def wait_time(self) -> float:
        """Seconds to wait before a token becomes available."""
        with self._lock:
            self._refill()
            if self.tokens >= 1:
                return 0.0
            return (1.0 - self.tokens) / self.refill_rate


class WhatsAppRateLimiter:
    """
    WhatsApp-specific rate limiter with per-account buckets.
    
    Usage:
        limiter = WhatsAppRateLimiter()
        
        # Before sending a message:
        if limiter.acquire(phone_number_id, mps_limit=5):
            send_message(...)
        else:
            # Rate limited - queue/delay the message
            wait_time = limiter.wait_time(phone_number_id)
            time.sleep(wait_time)
    """
    
    # Singleton-ish: share buckets across instances in the same process
    _buckets: Dict[str, TokenBucket] = {}
    _lock = threading.Lock()
    
    # Metrics
    _metrics: Dict[str, Dict] = {}
    
    def _get_bucket(self, phone_number_id: str, mps_limit: int = 5) -> TokenBucket:
        """Get or create token bucket for a phone number."""
        key = f"ratelimit:{phone_number_id}"
        
        with self._lock:
            if key not in self._buckets:
                # Capacity = 2x MPS for brief bursts
                self._buckets[key] = TokenBucket(
                    capacity=mps_limit * 2,
                    refill_rate=float(mps_limit),
                )
                self._metrics[key] = {
                    "total_requests": 0,
                    "total_limited": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            
            # Update capacity if mps_limit changed
            bucket = self._buckets[key]
            if bucket.refill_rate != float(mps_limit):
                bucket.capacity = mps_limit * 2
                bucket.refill_rate = float(mps_limit)
            
            return bucket
    
    def acquire(self, phone_number_id: str, mps_limit: int = 5, count: int = 1) -> bool:
        """
        Try to acquire rate limit tokens for sending.
        
        Args:
            phone_number_id: The phone number to rate limit
            mps_limit: Messages per second limit
            count: Number of tokens to acquire
            
        Returns:
            True if allowed to send, False if rate limited
        """
        bucket = self._get_bucket(phone_number_id, mps_limit)
        key = f"ratelimit:{phone_number_id}"
        
        with self._lock:
            self._metrics[key]["total_requests"] = self._metrics[key].get("total_requests", 0) + 1
        
        allowed = bucket.consume(count)
        
        if not allowed:
            with self._lock:
                self._metrics[key]["total_limited"] = self._metrics[key].get("total_limited", 0) + 1
            logger.warning(f"Rate limited: {phone_number_id} (limit={mps_limit} MPS)")
        
        return allowed
    
    def check_available(self, phone_number_id: str, mps_limit: int = 5) -> int:
        """Check available tokens without consuming."""
        bucket = self._get_bucket(phone_number_id, mps_limit)
        return bucket.available()
    
    def wait_time(self, phone_number_id: str, mps_limit: int = 5) -> float:
        """Get seconds to wait before next token is available."""
        bucket = self._get_bucket(phone_number_id, mps_limit)
        return bucket.wait_time()
    
    def get_metrics(self, phone_number_id: str) -> dict:
        """Get rate limit metrics for a phone number."""
        key = f"ratelimit:{phone_number_id}"
        with self._lock:
            return self._metrics.get(key, {}).copy()
    
    def reset(self, phone_number_id: str):
        """Reset rate limiter for a phone number (testing)."""
        key = f"ratelimit:{phone_number_id}"
        with self._lock:
            self._buckets.pop(key, None)
            self._metrics.pop(key, None)


# Global rate limiter instance
_rate_limiter = WhatsAppRateLimiter()


def get_rate_limiter() -> WhatsAppRateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter


def rate_limited_send(phone_number_id: str, mps_limit: int, send_func, *args, **kwargs):
    """
    Rate-limited wrapper for sending messages.
    
    If rate limited, waits for the appropriate time before sending.
    
    Args:
        phone_number_id: Account phone number ID
        mps_limit: Messages per second limit (5 for coexistence)
        send_func: Function to call for sending
        *args, **kwargs: Arguments to pass to send_func
        
    Returns:
        Result of send_func
    """
    limiter = get_rate_limiter()
    
    # Try to acquire immediately
    if limiter.acquire(phone_number_id, mps_limit):
        return send_func(*args, **kwargs)
    
    # Rate limited - wait and retry (up to 3 attempts)
    for attempt in range(3):
        wait = limiter.wait_time(phone_number_id, mps_limit)
        if wait > 0:
            logger.info(f"Rate limited, waiting {wait:.2f}s (attempt {attempt + 1}/3)")
            time.sleep(wait)
        
        if limiter.acquire(phone_number_id, mps_limit):
            return send_func(*args, **kwargs)
    
    # Still rate limited after retries
    logger.error(f"Rate limit exceeded after 3 retries for {phone_number_id}")
    return {
        "success": False,
        "error": "Rate limit exceeded (130429)",
        "error_code": "130429",
        "retry_after": limiter.wait_time(phone_number_id, mps_limit),
    }
