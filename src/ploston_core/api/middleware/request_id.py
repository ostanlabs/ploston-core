"""Request ID middleware."""

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable for request ID
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Get current request ID from context."""
    return request_id_var.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID to all requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Add request ID to request and response."""
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Store in context
        token = request_id_var.set(request_id)

        try:
            # Store in request state for handlers
            request.state.request_id = request_id

            # Process request
            response = await call_next(request)

            # Add to response headers
            response.headers["X-Request-ID"] = request_id

            return response
        finally:
            request_id_var.reset(token)

