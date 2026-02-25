"""Pro Auth middleware for principal-based authentication.

Implements PRO_AUTH_FOUNDATION_SPEC authentication flow:
1. Extract API key from Authorization header (Bearer token) or X-API-Key
2. Validate key against principal store
3. Attach PrincipalContext to request
4. Check scope for endpoint

For OSS mode (auth.mode: none): Attaches anonymous principal with full access.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .models import ANONYMOUS_PRINCIPAL, PrincipalContext, Scope

if TYPE_CHECKING:
    from .store import PrincipalStore

logger = logging.getLogger(__name__)

# Paths excluded from authentication
DEFAULT_EXCLUDE_PATHS = [
    "/health",
    "/info",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/runner/ws",  # Runner WebSocket uses its own auth
]

# Endpoint -> required scope mapping
ENDPOINT_SCOPES: dict[str, set[Scope]] = {
    # Read operations
    "GET:/api/v1/workflows": {Scope.READ},
    "GET:/api/v1/tools": {Scope.READ},
    "GET:/api/v1/executions": {Scope.READ},
    "GET:/api/v1/runners": {Scope.READ},
    "GET:/api/v1/config": {Scope.READ},
    # Execute operations
    "POST:/api/v1/workflows/*/execute": {Scope.EXECUTE},
    "POST:/api/v1/tools/call": {Scope.EXECUTE},
    "POST:/mcp": {Scope.EXECUTE},  # MCP endpoint
    # Write operations
    "POST:/api/v1/workflows": {Scope.WRITE},
    "PUT:/api/v1/workflows": {Scope.WRITE},
    "DELETE:/api/v1/workflows": {Scope.WRITE},
    "POST:/api/v1/config": {Scope.WRITE},
    "PUT:/api/v1/config": {Scope.WRITE},
    # Admin operations
    "POST:/api/v1/principals": {Scope.ADMIN},
    "PUT:/api/v1/principals": {Scope.ADMIN},
    "DELETE:/api/v1/principals": {Scope.ADMIN},
    "POST:/api/v1/principals/*/rotate": {Scope.ADMIN},
}


def _extract_api_key(request: Request) -> str | None:
    """Extract API key from request headers.

    Checks:
    1. Authorization: Bearer plt_xxx
    2. X-API-Key: plt_xxx
    """
    # Check Authorization header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # Fall back to X-API-Key
    return request.headers.get("X-API-Key")


def _get_required_scope(method: str, path: str) -> set[Scope] | None:
    """Get required scope for an endpoint.

    Returns None if endpoint doesn't require specific scope (default: READ).
    """
    # Try exact match first
    key = f"{method}:{path}"
    if key in ENDPOINT_SCOPES:
        return ENDPOINT_SCOPES[key]

    # Try wildcard matches
    for pattern, scopes in ENDPOINT_SCOPES.items():
        pattern_method, pattern_path = pattern.split(":", 1)
        if pattern_method != method:
            continue

        # Simple wildcard matching
        if "*" in pattern_path:
            pattern_parts = pattern_path.split("*")
            if len(pattern_parts) == 2:
                prefix, suffix = pattern_parts
                if path.startswith(prefix) and path.endswith(suffix):
                    return scopes

    # Default: READ scope for GET, EXECUTE for POST
    if method == "GET":
        return {Scope.READ}
    elif method in ("POST", "PUT", "DELETE"):
        return {Scope.EXECUTE}

    return {Scope.READ}


class ProAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for Pro principal-based authentication.

    When auth_enabled=True:
    - Validates API key against principal store
    - Attaches PrincipalContext to request.state
    - Checks scope for endpoint

    When auth_enabled=False (OSS mode):
    - Attaches anonymous principal with full access
    """

    def __init__(
        self,
        app,
        principal_store: PrincipalStore | None = None,
        auth_enabled: bool = False,
        exclude_paths: list[str] | None = None,
    ):
        """Initialize middleware.

        Args:
            app: ASGI application
            principal_store: Store for principal data (required if auth_enabled)
            auth_enabled: Whether to enforce authentication
            exclude_paths: Paths to exclude from authentication
        """
        super().__init__(app)
        self._store = principal_store
        self._auth_enabled = auth_enabled
        self._exclude_paths = exclude_paths or DEFAULT_EXCLUDE_PATHS

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with authentication."""
        path = request.url.path

        # Skip auth for excluded paths
        if any(path.startswith(excluded) for excluded in self._exclude_paths):
            return await call_next(request)

        # OSS mode: attach anonymous principal
        if not self._auth_enabled:
            request.state.principal_context = PrincipalContext(
                principal=ANONYMOUS_PRINCIPAL,
                api_key_prefix="none",
            )
            return await call_next(request)

        # Pro mode: validate API key
        api_key = _extract_api_key(request)
        if not api_key:
            return self._unauthorized("Invalid or missing API key")

        # Validate key and get principal
        if not self._store:
            return self._error(500, "AUTH_NOT_CONFIGURED", "Auth store not configured")

        principal = await self._store.validate_key(api_key)
        if not principal:
            return self._unauthorized("Invalid or missing API key")

        if not principal.enabled:
            return self._forbidden(f"Principal '{principal.name}' is disabled")

        # Attach context to request
        from .api_key import extract_key_prefix

        request.state.principal_context = PrincipalContext(
            principal=principal,
            api_key_prefix=extract_key_prefix(api_key),
        )

        # Check scope
        required_scopes = _get_required_scope(request.method, path)
        if required_scopes and not principal.has_any_scope(required_scopes):
            scope_names = ", ".join(s.value for s in required_scopes)
            return self._forbidden(
                f"Principal '{principal.name}' lacks scope '{scope_names}' for this endpoint"
            )

        return await call_next(request)

    def _unauthorized(self, message: str) -> JSONResponse:
        """Return 401 Unauthorized response."""
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": message,
                }
            },
        )

    def _forbidden(self, message: str) -> JSONResponse:
        """Return 403 Forbidden response."""
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": message,
                }
            },
        )

    def _error(self, status: int, code: str, message: str) -> JSONResponse:
        """Return error response."""
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                }
            },
        )
