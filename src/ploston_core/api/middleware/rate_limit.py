"""Rate limiting middleware."""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class RateLimitState:
    """State for a single client."""

    requests: list[float] = field(default_factory=list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting requests."""

    def __init__(
        self,
        app,
        requests_per_minute: int = 100,
        exclude_paths: list[str] | None = None,
    ):
        """Initialize middleware.

        Args:
            app: ASGI application
            requests_per_minute: Maximum requests per minute per client
            exclude_paths: Paths to exclude from rate limiting
        """
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self.exclude_paths = exclude_paths or ["/health", "/info"]
        self.clients: dict[str, RateLimitState] = defaultdict(RateLimitState)

    def _get_client_id(self, request: Request) -> str:
        """Get client identifier from request."""
        # Use API key if available, otherwise use IP
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"key:{api_key[:8]}"

        # Use forwarded IP if behind proxy
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"

        # Use direct client IP
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    def _is_rate_limited(self, client_id: str) -> tuple[bool, int]:
        """Check if client is rate limited.

        Returns:
            Tuple of (is_limited, remaining_requests)
        """
        now = time.time()
        state = self.clients[client_id]

        # Remove old requests outside window
        cutoff = now - self.window_seconds
        state.requests = [t for t in state.requests if t > cutoff]

        # Check limit
        remaining = self.requests_per_minute - len(state.requests)
        if remaining <= 0:
            return True, 0

        # Record this request
        state.requests.append(now)
        return False, remaining - 1

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check rate limit before processing request."""
        # Skip rate limiting for excluded paths
        path = request.url.path
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            return await call_next(request)

        client_id = self._get_client_id(request)
        is_limited, remaining = self._is_rate_limited(client_id)

        if is_limited:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "category": "SYSTEM",
                        "message": "Rate limit exceeded",
                        "detail": f"Maximum {self.requests_per_minute} requests per minute",
                        "suggestion": "Wait before making more requests",
                    }
                },
                headers={
                    "X-RateLimit-Limit": str(self.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": "60",
                },
            )

        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response

