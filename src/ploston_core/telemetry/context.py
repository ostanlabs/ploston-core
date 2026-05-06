"""Shared ContextVars for direct tool call request scope.

Placed here (not in mcp_frontend/server.py) to avoid circular imports
when AELLogger reads it.
"""

from contextvars import ContextVar

direct_execution_id: ContextVar[str | None] = ContextVar("direct_execution_id", default=None)
direct_session_id: ContextVar[str | None] = ContextVar("direct_session_id", default=None)
