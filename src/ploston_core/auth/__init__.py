"""Pro Auth Foundation - Authentication and Identity Layer.

Implements PRO_AUTH_FOUNDATION_SPEC:
- Principal model (authenticated entities)
- Scopes (read/execute/write/admin)
- Tool access control (allowlist/denylist)
- API key management
- Rate limiting

For OSS (auth.mode: none): All auth is bypassed, anonymous principal with full access.
For Pro (auth.mode: pro): Full principal-based auth with scopes and tool filtering.
"""

from .api_key import (
    extract_key_prefix,
    generate_api_key,
    hash_api_key,
    validate_api_key_format,
    verify_api_key,
)
from .middleware import ProAuthMiddleware
from .models import (
    ANONYMOUS_PRINCIPAL,
    Principal,
    PrincipalContext,
    PrincipalSettings,
    PrincipalType,
    Scope,
    ToolAccess,
    ToolAccessMode,
)
from .rate_limiter import RateLimiter
from .store import PrincipalStore
from .tool_filter import can_access_tool, filter_tools_by_principal

__all__ = [
    # Models
    "ANONYMOUS_PRINCIPAL",
    "Principal",
    "PrincipalContext",
    "PrincipalSettings",
    "PrincipalType",
    "Scope",
    "ToolAccess",
    "ToolAccessMode",
    # API Key
    "extract_key_prefix",
    "generate_api_key",
    "hash_api_key",
    "validate_api_key_format",
    "verify_api_key",
    # Middleware
    "ProAuthMiddleware",
    # Store
    "PrincipalStore",
    # Rate limiting
    "RateLimiter",
    # Tool filtering
    "can_access_tool",
    "filter_tools_by_principal",
]
