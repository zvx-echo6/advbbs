"""
advBBS Rate Limiter

Prevents mesh flooding by limiting message rates per sender.
Essential for mesh network health.
"""

import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class RateBucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_update: float
    max_tokens: int
    refill_rate: float  # tokens per second

    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from the bucket.

        Returns True if tokens were consumed, False if rate limited.
        """
        now = time.time()

        # Refill tokens based on time elapsed
        elapsed = now - self.last_update
        self.tokens = min(
            self.max_tokens,
            self.tokens + (elapsed * self.refill_rate)
        )
        self.last_update = now

        # Try to consume
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    def time_until_ready(self, tokens: int = 1) -> float:
        """Return seconds until enough tokens are available."""
        if self.tokens >= tokens:
            return 0.0

        needed = tokens - self.tokens
        return needed / self.refill_rate


class RateLimiter:
    """
    Rate limiter for mesh message handling.

    Uses token bucket algorithm with separate limits for:
    - Regular commands
    - Sync messages
    - General messages
    """

    def __init__(
        self,
        messages_per_minute: int = 10,
        sync_messages_per_minute: int = 20,
        commands_per_minute: int = 30
    ):
        """
        Initialize rate limiter with per-minute limits.

        Args:
            messages_per_minute: General message limit
            sync_messages_per_minute: Inter-BBS sync message limit
            commands_per_minute: BBS command limit
        """
        self.limits = {
            "message": messages_per_minute,
            "sync": sync_messages_per_minute,
            "command": commands_per_minute,
        }

        # Per-sender buckets: {sender_id: {bucket_type: RateBucket}}
        self._buckets: Dict[str, Dict[str, RateBucket]] = defaultdict(dict)

        # Global buckets for overall system protection
        self._global_buckets: Dict[str, RateBucket] = {}

        self._init_global_buckets()

        logger.debug(f"RateLimiter initialized: {self.limits}")

    def _init_global_buckets(self):
        """Initialize global rate limit buckets."""
        now = time.time()

        for bucket_type, limit in self.limits.items():
            # Global limit is 10x per-sender limit
            self._global_buckets[bucket_type] = RateBucket(
                tokens=limit * 10,
                last_update=now,
                max_tokens=limit * 10,
                refill_rate=(limit * 10) / 60.0
            )

    def _get_bucket(self, sender: str, bucket_type: str) -> RateBucket:
        """Get or create a rate bucket for a sender."""
        if bucket_type not in self._buckets[sender]:
            limit = self.limits.get(bucket_type, self.limits["message"])
            self._buckets[sender][bucket_type] = RateBucket(
                tokens=limit,
                last_update=time.time(),
                max_tokens=limit,
                refill_rate=limit / 60.0  # Refill over 1 minute
            )

        return self._buckets[sender][bucket_type]

    def check(self, sender: str, bucket_type: str = "message") -> bool:
        """
        Check if a message should be allowed.

        Args:
            sender: Sender node ID
            bucket_type: Type of rate limit ("message", "sync", "command")

        Returns:
            True if allowed, False if rate limited
        """
        # Check global limit first
        global_bucket = self._global_buckets.get(bucket_type)
        if global_bucket and not global_bucket.consume():
            logger.warning(f"Global rate limit exceeded for {bucket_type}")
            return False

        # Check per-sender limit
        bucket = self._get_bucket(sender, bucket_type)
        allowed = bucket.consume()

        if not allowed:
            logger.warning(f"Rate limit exceeded for {sender} ({bucket_type})")

        return allowed

    def time_until_allowed(
        self,
        sender: str,
        bucket_type: str = "message"
    ) -> float:
        """Return seconds until sender can send again."""
        bucket = self._get_bucket(sender, bucket_type)
        return bucket.time_until_ready()

    def cleanup(self, max_age_seconds: int = 300):
        """
        Remove stale buckets to free memory.

        Call periodically (e.g., every 5 minutes).
        """
        now = time.time()
        stale_senders = []

        for sender, buckets in self._buckets.items():
            # Check if all buckets are stale
            all_stale = all(
                now - b.last_update > max_age_seconds
                for b in buckets.values()
            )
            if all_stale:
                stale_senders.append(sender)

        for sender in stale_senders:
            del self._buckets[sender]

        if stale_senders:
            logger.debug(f"Cleaned up {len(stale_senders)} stale rate limit buckets")

    def get_stats(self) -> dict:
        """Return rate limiter statistics."""
        return {
            "active_senders": len(self._buckets),
            "limits": self.limits,
            "global_buckets": {
                name: {
                    "tokens": bucket.tokens,
                    "max": bucket.max_tokens,
                }
                for name, bucket in self._global_buckets.items()
            }
        }


class CommandThrottler:
    """
    Specialized throttler for command responses.

    Ensures we don't flood the mesh with responses even if
    we receive many commands quickly.
    """

    def __init__(self, min_response_delay: float = 0.5, max_queue_size: int = 10):
        """
        Initialize command throttler.

        Args:
            min_response_delay: Minimum seconds between responses
            max_queue_size: Maximum pending responses per sender
        """
        self.min_delay = min_response_delay
        self.max_queue = max_queue_size
        self._last_response: Dict[str, float] = {}
        self._queue_sizes: Dict[str, int] = defaultdict(int)

    def should_respond(self, sender: str) -> tuple[bool, float]:
        """
        Check if we should respond to a command.

        Returns:
            (should_respond, delay_seconds) tuple
        """
        now = time.time()

        # Check queue size
        if self._queue_sizes[sender] >= self.max_queue:
            logger.warning(f"Response queue full for {sender}")
            return False, 0.0

        # Check timing
        last = self._last_response.get(sender, 0)
        elapsed = now - last

        if elapsed < self.min_delay:
            delay = self.min_delay - elapsed
            return True, delay

        return True, 0.0

    def record_response(self, sender: str):
        """Record that we sent a response."""
        self._last_response[sender] = time.time()

    def queue_response(self, sender: str):
        """Increment queue counter for sender."""
        self._queue_sizes[sender] += 1

    def dequeue_response(self, sender: str):
        """Decrement queue counter for sender."""
        if self._queue_sizes[sender] > 0:
            self._queue_sizes[sender] -= 1
