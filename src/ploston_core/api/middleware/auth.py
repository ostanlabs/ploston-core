"""API Key authentication middleware."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ploston_core.api.config import APIKeyConfig


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication."""

    def __init__(self, app, api_keys: list[APIKeyConfig], exclude_paths: list[str] | None = None):
        """Initialize middleware.

        Args:
            app: ASGI application
            api_keys: List of valid API key configurations
            exclude_paths: Paths to exclude from authentication (e.g., /health)
        """
        super().__init__(app)
        self.api_keys = {key.key: key for key in api_keys}
        self.exclude_paths = exclude_paths or ["/health", "/info", "/docs", "/redoc", "/openapi.json"]

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check API key authentication."""
        # Skip auth for excluded paths
        path = request.url.path
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            return await call_next(request)

        # Get API key from header
        api_key = request.headers.get("X-API-Key")

        if not api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "AUTH_MISSING_KEY",
                        "category": "VALIDATION",
                        "message": "API key required",
                        "detail": "Include X-API-Key header with a valid API key",
                    }
                },
            )

        # Validate API key
        key_config = self.api_keys.get(api_key)
        if not key_config:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "AUTH_INVALID_KEY",
                        "category": "VALIDATION",
                        "message": "Invalid API key",
                    }
                },
            )

        # Store key info in request state
        request.state.api_key_name = key_config.name
        request.state.api_key_scopes = key_config.scopes

        return await call_next(request)

