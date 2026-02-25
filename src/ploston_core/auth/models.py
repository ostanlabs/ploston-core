"""Principal and auth models for Pro Auth Foundation.

Implements PRO_AUTH_FOUNDATION_SPEC core concepts:
- Principal: Authenticated entity (user, service)
- Scope: What operations a principal can perform
- ToolAccess: Which MCP servers a principal can access
- PrincipalSettings: Per-principal customization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PrincipalType(str, Enum):
    """Type of principal."""

    USER = "user"  # Human user (Marc, team member)
    SERVICE = "service"  # Non-human (CI/CD bot, agent bridge)


class Scope(str, Enum):
    """Operations a principal can perform.

    Scope implications (not inheritance — just documentation):
    - READ: GET endpoints, tools/list, workflow definitions
    - EXECUTE: POST /execute, tools/call — requires READ implicitly
    - WRITE: PUT/DELETE workflows, config changes — requires READ implicitly
    - ADMIN: Principal CRUD, token management, system settings — requires all
    """

    READ = "read"
    EXECUTE = "execute"
    WRITE = "write"
    ADMIN = "admin"


class ToolAccessMode(str, Enum):
    """Tool access control mode."""

    ALL = "all"  # See all tools (default)
    ALLOWLIST = "allowlist"  # Only see tools from listed servers
    DENYLIST = "denylist"  # See all except tools from listed servers


@dataclass
class ToolAccess:
    """Server-level tool access control."""

    mode: ToolAccessMode = ToolAccessMode.ALL
    servers: list[str] = field(default_factory=list)

    def can_access_server(self, server_name: str) -> bool:
        """Check if principal can access tools from a server."""
        if self.mode == ToolAccessMode.ALL:
            return True
        if self.mode == ToolAccessMode.ALLOWLIST:
            return server_name in self.servers
        if self.mode == ToolAccessMode.DENYLIST:
            return server_name not in self.servers
        return False

    def filter_servers(self, server_names: list[str]) -> list[str]:
        """Filter a list of servers to only those accessible."""
        return [s for s in server_names if self.can_access_server(s)]


@dataclass
class PrincipalSettings:
    """Per-principal settings overlay."""

    default_timeout: int | None = None  # Override system default
    log_level: str | None = None  # Override system log level
    rate_limit: int | None = None  # Requests per minute (None = system default)
    tool_name_prefix: str | None = None  # Override default naming (Pro feature)


@dataclass
class Principal:
    """Authenticated entity in Ploston Pro.

    A principal is any authenticated entity interacting with Ploston.
    It can be a human user, an agent (via bridge), or a service account (CI/CD).
    """

    # Identity
    id: str  # Unique ID: "usr_xxxx" or "svc_xxxx"
    name: str  # Human-readable: "marc", "claude-bridge", "ci-bot"
    type: PrincipalType

    # Authentication (key hash stored separately in Redis)
    api_key_prefix: str  # First 8 chars for identification: "plt_marc"
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None

    # Authorization
    scopes: set[Scope] = field(default_factory=lambda: {Scope.READ})
    tool_access: ToolAccess = field(default_factory=ToolAccess)

    # Settings (per-principal customization)
    settings: PrincipalSettings = field(default_factory=PrincipalSettings)

    # Metadata
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def has_scope(self, scope: Scope) -> bool:
        """Check if principal has a specific scope."""
        return scope in self.scopes

    def has_any_scope(self, scopes: set[Scope]) -> bool:
        """Check if principal has any of the specified scopes."""
        return bool(self.scopes & scopes)

    def can_access_tool(self, server_name: str) -> bool:
        """Check if principal can access tools from a server."""
        return self.tool_access.can_access_server(server_name)


@dataclass
class PrincipalContext:
    """Request context with resolved principal.

    Attached to request.state after auth middleware resolves the API key.
    """

    principal: Principal
    api_key_prefix: str  # For logging (never log full key)
    authenticated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def principal_id(self) -> str:
        """Get principal ID for telemetry."""
        return self.principal.id

    @property
    def principal_type(self) -> str:
        """Get principal type for telemetry."""
        return self.principal.type.value


# Anonymous principal for OSS mode (auth.mode: none)
ANONYMOUS_PRINCIPAL = Principal(
    id="anon",
    name="anonymous",
    type=PrincipalType.USER,
    api_key_prefix="none",
    scopes={Scope.READ, Scope.EXECUTE, Scope.WRITE, Scope.ADMIN},
    tool_access=ToolAccess(mode=ToolAccessMode.ALL),
    enabled=True,
)
