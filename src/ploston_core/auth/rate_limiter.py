"""Per-principal rate limiting for Pro Auth Foundation.

Implements PRO_AUTH_FOUNDATION_SPEC rate limiting:
- Token bucket algorithm
- Per-principal limits stored in Redis
- Atomic check-and-decrement via Lua script

Defaults:
- Bucket size: 60 (max tokens / burst capacity)
- Refill rate: 60/min (tokens added per minute)
- Per-principal override via settings.rate_limit
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ploston_core.config.redis_store import RedisConfigStore

logger = logging.getLogger(__name__)

# Default rate limit settings
DEFAULT_BUCKET_SIZE = 60  # Max tokens (burst capacity)
DEFAULT_REFILL_RATE = 60  # Tokens per minute
DEFAULT_WINDOW_SECONDS = 60  # 1 minute window

# Redis Lua script for atomic rate limiting
# Returns 1 if allowed, 0 if rate limited
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or limit
local last_refill = tonumber(data[2]) or now

-- Refill tokens based on elapsed time
local elapsed = now - last_refill
local refill = math.floor(elapsed * limit / window)
tokens = math.min(limit, tokens + refill)

if tokens > 0 then
    redis.call('HMSET', key, 'tokens', tokens - 1, 'last_refill', now)
    redis.call('EXPIRE', key, window * 2)
    return 1  -- allowed
else
    return 0  -- rate limited
end
"""


class RateLimiter:
    """Per-principal rate limiter using token bucket algorithm.

    Uses Redis for distributed rate limiting with atomic operations.
    Falls back to allowing requests if Redis is unavailable (fail-open).
    """

    def __init__(
        self,
        redis_store: RedisConfigStore,
        default_limit: int = DEFAULT_BUCKET_SIZE,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ):
        """Initialize rate limiter.

        Args:
            redis_store: Redis config store for persistence
            default_limit: Default requests per minute
            window_seconds: Time window in seconds
        """
        self._redis = redis_store
        self._default_limit = default_limit
        self._window_seconds = window_seconds
        self._script_sha: str | None = None

    async def _ensure_script(self) -> str | None:
        """Load Lua script into Redis and cache SHA."""
        if self._script_sha:
            return self._script_sha

        if not self._redis.connected or not self._redis._client:
            return None

        try:
            self._script_sha = await self._redis._client.script_load(RATE_LIMIT_SCRIPT)
            return self._script_sha
        except Exception as e:
            logger.warning(f"Failed to load rate limit script: {e}")
            return None

    async def check_rate_limit(
        self,
        principal_id: str,
        custom_limit: int | None = None,
    ) -> tuple[bool, int]:
        """Check if request is allowed under rate limit.

        Args:
            principal_id: Principal ID to check
            custom_limit: Optional custom limit (from principal settings)

        Returns:
            Tuple of (allowed: bool, retry_after_seconds: int)
            retry_after is 0 if allowed, otherwise seconds until next token
        """
        if not self._redis.connected:
            # Fail-open: allow if Redis unavailable
            logger.warning("[AUTH] Redis unavailable. Rate limiting disabled.")
            return (True, 0)

        limit = custom_limit or self._default_limit
        key = f"rate_limit:{principal_id}"
        now = int(time.time())

        try:
            script_sha = await self._ensure_script()
            if not script_sha:
                return (True, 0)  # Fail-open

            result = await self._redis._client.evalsha(
                script_sha,
                1,  # number of keys
                key,
                limit,
                self._window_seconds,
                now,
            )

            if result == 1:
                return (True, 0)
            else:
                # Rate limited - calculate retry_after
                retry_after = self._window_seconds // limit
                return (False, retry_after)

        except Exception as e:
            logger.warning(f"Rate limit check failed: {e}")
            return (True, 0)  # Fail-open

    async def get_remaining_tokens(self, principal_id: str) -> int | None:
        """Get remaining tokens for a principal (for headers/debugging).

        Returns None if Redis unavailable.
        """
        if not self._redis.connected or not self._redis._client:
            return None

        try:
            key = f"rate_limit:{principal_id}"
            data = await self._redis._client.hget(key, "tokens")
            return int(data) if data else self._default_limit
        except Exception:
            return None
