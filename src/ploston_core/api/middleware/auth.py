"""API Key authentication middleware."""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ploston_core.api.config import APIKeyConfig

logger = logging.getLogger(__name__)

# Scopes representing unrestricted ("full") access. A key configured with
# exactly these (the default) is considered full access and is not flagged.
# This mirrors APIKeyConfig's default factory.
_FULL_ACCESS_SCOPES = frozenset({"read", "write", "execute"})


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication.

    Note (open-core): per-API-key ``scopes`` are accepted and stored on the
    request state, but scope *enforcement* is a Pro-tier feature and is NOT
    implemented in OSS. To avoid silently misleading operators, configuring a
    key with anything other than full access emits a one-time warning at
    construction time stating that scopes are not enforced in OSS.
    """

    # Process-wide guard so the OSS scope advisory is logged only once even if
    # the middleware is reconstructed (multiple apps / per-request rebuilds).
    _scope_warning_emitted: bool = False

    def __init__(self, app, api_keys: list[APIKeyConfig], exclude_paths: list[str] | None = None):
        """Initialize middleware.

        Args:
            app: ASGI application
            api_keys: List of valid API key configurations
            exclude_paths: Paths to exclude from authentication (e.g., /health)
        """
        super().__init__(app)
        self.api_keys = {key.key: key for key in api_keys}

        # Honesty warning: surface that restricted scopes are not enforced in OSS.
        # Emitted at most once per process (the middleware stack may be rebuilt
        # repeatedly — e.g. per request under some test clients — but the
        # advisory only needs to be seen once).
        restricted = [key.name for key in api_keys if set(key.scopes) != _FULL_ACCESS_SCOPES]
        if restricted and not APIKeyAuthMiddleware._scope_warning_emitted:
            APIKeyAuthMiddleware._scope_warning_emitted = True
            logger.warning(
                "API key(s) %s are configured with restricted scopes, but per-key "
                "scope enforcement is NOT enforced in OSS — it requires the Pro "
                "tier. These scopes are accepted and recorded but grant no "
                "additional protection here.",
                ", ".join(repr(name) for name in restricted),
            )
        # H-6: exact-match set (not prefix match) to prevent path-prefix bypass
        # such as /docsanything or /healthx slipping past authentication.
        self.exclude_paths = set(
            exclude_paths
            or [
                "/health",
                "/info",
                "/docs",
                "/redoc",
                "/openapi.json",
            ]
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check API key authentication."""
        # Skip auth for excluded paths (exact match only - H-6)
        path = request.url.path
        if path in self.exclude_paths:
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
