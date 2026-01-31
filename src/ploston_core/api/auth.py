"""Authentication hook for REST API.

Implements S-188: Auth Hook Pattern
- T-533: Auth hook pattern (no-op for OSS)

The AuthHook provides a pluggable authentication/authorization mechanism.
In OSS (ploston), it's a no-op that allows all requests.
In Enterprise (ploston-enterprise), it can be overridden to enforce RBAC.

Usage:
    from ploston_core.api.auth import auth_hook

    @router.post("/api/v1/runners")
    async def create_runner(request: Request, body: CreateRunnerRequest):
        await auth_hook.check_permission(request, "runner:create")
        # ... rest of handler

Enterprise override:
    from ploston_core.api.auth import AuthHook
    import ploston_core.api.auth as auth_module

    class EnterpriseAuthHook(AuthHook):
        async def check_permission(self, request: Request, permission: str) -> None:
            user = await self.get_user(request)
            if not user.has_permission(permission):
                raise HTTPException(403, "Permission denied")

    # Replace at startup
    auth_module.auth_hook = EnterpriseAuthHook()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


class AuthHook:
    """Authentication/authorization hook for REST API.

    This is the base class that provides a no-op implementation.
    All requests are allowed by default (OSS behavior).

    Enterprise can subclass this to implement RBAC, license checks, etc.
    """

    async def check_permission(self, request: Request, permission: str) -> None:
        """Check if the request has the required permission.

        Args:
            request: FastAPI request object
            permission: Permission string (e.g., "runner:create", "runner:list")

        Raises:
            HTTPException: If permission is denied (not in OSS)

        In OSS, this is a no-op that always allows the request.
        Enterprise overrides this to enforce RBAC.
        """
        # OSS: Always allow
        logger.debug(f"Auth check (OSS no-op): {permission}")

    async def get_user(self, request: Request) -> Any:
        """Get the authenticated user from the request.

        Args:
            request: FastAPI request object

        Returns:
            User object or None if not authenticated

        In OSS, returns None (no user tracking).
        Enterprise overrides this to return the authenticated user.
        """
        return None

    async def get_user_id(self, request: Request) -> str | None:
        """Get the authenticated user ID from the request.

        Args:
            request: FastAPI request object

        Returns:
            User ID string or None if not authenticated
        """
        user = await self.get_user(request)
        if user and hasattr(user, "id"):
            return user.id
        return None


# Global auth hook instance
# Enterprise can replace this at startup
auth_hook = AuthHook()


# Permission constants for runner management
class RunnerPermissions:
    """Permission constants for runner management."""

    CREATE = "runner:create"
    LIST = "runner:list"
    READ = "runner:read"
    DELETE = "runner:delete"
    UPDATE = "runner:update"
