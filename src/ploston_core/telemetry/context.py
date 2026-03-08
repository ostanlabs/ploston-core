"""Shared ContextVar for direct tool call execution ID.

Placed here (not in mcp_frontend/server.py) to avoid circular imports
when AELLogger reads it.
"""

from contextvars import ContextVar

direct_execution_id: ContextVar[str | None] = ContextVar("direct_execution_id", default=None)
