"""Tool Registry - central catalog of all available tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ploston_core.config.models import ToolsConfig
from ploston_core.errors import create_error
from ploston_core.logging.logger import AELLogger
from ploston_core.mcp.manager import MCPClientManager
from ploston_core.types import LogLevel, ToolSource, ToolStatus

from .types import RefreshResult, ToolDefinition, ToolRouter


class ToolRegistry:
    """Central registry of all available tools.

    Aggregates tools from:
    - MCP servers (via MCP Client Manager)
    - System tools (python_exec)

    Provides:
    - Tool discovery
    - Schema access
    - Source routing (which server handles which tool)
    """

    def __init__(
        self,
        mcp_manager: MCPClientManager,
        config: ToolsConfig,
        logger: AELLogger | None = None,
    ):
        """Initialize tool registry.

        Args:
            mcp_manager: MCP client manager for fetching tools
            config: Tools configuration
            logger: Optional logger
        """
        self._tools: dict[str, ToolDefinition] = {}
        self._mcp_manager = mcp_manager
        self._config = config
        self._logger = logger

    def _log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """Log message if logger available."""
        if self._logger:
            self._logger._log(level, "registry", message, **kwargs)

    async def initialize(self) -> RefreshResult:
        """Initialize registry.

        - Connect to MCP servers
        - Fetch all tools
        - Register system tools

        Returns:
            RefreshResult with initial load summary
        """
        self._log(LogLevel.INFO, "Initializing tool registry")

        # Register system tools first
        self._register_system_tools()

        # Connect to MCP servers and fetch tools
        await self._mcp_manager.connect_all()
        result = await self.refresh()

        self._log(
            LogLevel.INFO,
            f"Tool registry initialized with {result.total_tools} tools",
        )

        return result

    async def refresh(self) -> RefreshResult:
        """Refresh tools from all sources.

        - Refresh from MCP servers
        - Update availability status
        - Detect added/removed/updated tools

        Returns:
            RefreshResult with changes
        """
        self._log(LogLevel.INFO, "Refreshing tools from all sources")

        # Track changes
        old_tools = set(self._tools.keys())
        added: list[str] = []
        removed: list[str] = []
        updated: list[str] = []
        errors: dict[str, str] = {}

        # Refresh from MCP servers
        tools_by_server = await self._mcp_manager.refresh_all()

        # Update tools from each server
        new_tools: set[str] = set()
        for server_name, tools in tools_by_server.items():
            for tool_schema in tools:
                tool_name = tool_schema.name

                # Check if tool already exists
                if tool_name in self._tools:
                    # Update existing tool
                    existing = self._tools[tool_name]
                    if (
                        existing.description != tool_schema.description
                        or existing.input_schema != tool_schema.input_schema
                    ):
                        updated.append(tool_name)

                    # Update fields
                    existing.description = tool_schema.description
                    existing.input_schema = tool_schema.input_schema
                    existing.output_schema = tool_schema.output_schema
                    existing.status = ToolStatus.AVAILABLE
                    existing.last_seen = datetime.now(UTC)
                    existing.error = None
                else:
                    # Add new tool
                    self._tools[tool_name] = ToolDefinition(
                        name=tool_name,
                        description=tool_schema.description,
                        source=ToolSource.MCP,
                        server_name=server_name,
                        input_schema=tool_schema.input_schema,
                        output_schema=tool_schema.output_schema,
                        status=ToolStatus.AVAILABLE,
                        last_seen=datetime.now(UTC),
                    )
                    added.append(tool_name)

                new_tools.add(tool_name)

        # Mark removed MCP tools as unavailable (don't delete)
        for tool_name in old_tools:
            tool = self._tools[tool_name]
            if tool.source == ToolSource.MCP and tool_name not in new_tools:
                tool.status = ToolStatus.UNAVAILABLE
                removed.append(tool_name)

        result = RefreshResult(
            total_tools=len(self._tools),
            added=added,
            removed=removed,
            updated=updated,
            errors=errors,
        )

        self._log(
            LogLevel.INFO,
            f"Refresh complete: {len(added)} added, {len(removed)} removed, {len(updated)} updated",
        )

        return result

    async def refresh_server(self, server_name: str) -> RefreshResult:
        """Refresh tools from a specific MCP server.

        Args:
            server_name: Name of MCP server to refresh

        Returns:
            RefreshResult with changes from this server
        """
        self._log(LogLevel.INFO, f"Refreshing tools from server '{server_name}'")

        # Get connection
        conn = self._mcp_manager.get_connection(server_name)
        if not conn:
            return RefreshResult(
                total_tools=len(self._tools),
                added=[],
                removed=[],
                updated=[],
                errors={server_name: "Server not found"},
            )

        # Track changes
        added: list[str] = []
        removed: list[str] = []
        updated: list[str] = []
        errors: dict[str, str] = {}

        try:
            # Refresh tools from this server
            tools = await conn.refresh_tools()

            # Track which tools we saw from this server
            seen_tools: set[str] = set()

            for tool_schema in tools:
                tool_name = tool_schema.name
                seen_tools.add(tool_name)

                if tool_name in self._tools:
                    # Update existing tool
                    existing = self._tools[tool_name]
                    if (
                        existing.description != tool_schema.description
                        or existing.input_schema != tool_schema.input_schema
                    ):
                        updated.append(tool_name)

                    existing.description = tool_schema.description
                    existing.input_schema = tool_schema.input_schema
                    existing.output_schema = tool_schema.output_schema
                    existing.status = ToolStatus.AVAILABLE
                    existing.last_seen = datetime.now(UTC)
                    existing.error = None
                else:
                    # Add new tool
                    self._tools[tool_name] = ToolDefinition(
                        name=tool_name,
                        description=tool_schema.description,
                        source=ToolSource.MCP,
                        server_name=server_name,
                        input_schema=tool_schema.input_schema,
                        output_schema=tool_schema.output_schema,
                        status=ToolStatus.AVAILABLE,
                        last_seen=datetime.now(UTC),
                    )
                    added.append(tool_name)

            # Mark tools from this server that are no longer present
            for tool_name, tool in self._tools.items():
                if (
                    tool.source == ToolSource.MCP
                    and tool.server_name == server_name
                    and tool_name not in seen_tools
                ):
                    tool.status = ToolStatus.UNAVAILABLE
                    removed.append(tool_name)

        except Exception as e:
            errors[server_name] = str(e)
            self._log(LogLevel.ERROR, f"Failed to refresh server '{server_name}': {e}")

        return RefreshResult(
            total_tools=len(self._tools),
            added=added,
            removed=removed,
            updated=updated,
            errors=errors,
        )

    def get(self, name: str) -> ToolDefinition | None:
        """Get tool by name.

        Args:
            name: Tool name

        Returns:
            ToolDefinition or None if not found
        """
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> ToolDefinition:
        """Get tool by name, raise if not found.

        Args:
            name: Tool name

        Returns:
            ToolDefinition

        Raises:
            AELError(TOOL_UNAVAILABLE) if not found
        """
        tool = self.get(name)
        if tool is None:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Tool '{name}' not found in registry",
            )
        return tool

    def list_tools(
        self,
        source: ToolSource | None = None,
        server_name: str | None = None,
        status: ToolStatus | None = None,
    ) -> list[ToolDefinition]:
        """List tools with optional filtering.

        Args:
            source: Filter by source type
            server_name: Filter by MCP server
            status: Filter by availability status

        Returns:
            List of matching tools
        """
        tools_list: list[ToolDefinition] = [t for t in self._tools.values()]

        if source is not None:
            tools_list = [t for t in tools_list if t.source == source]

        if server_name is not None:
            tools_list = [t for t in tools_list if t.server_name == server_name]

        if status is not None:
            tools_list = [t for t in tools_list if t.status == status]

        return tools_list

    def list_available(self) -> list[ToolDefinition]:
        """List only available tools.

        Returns:
            List of available tools
        """
        return self.list_tools(status=ToolStatus.AVAILABLE)

    def search(self, query: str) -> list[ToolDefinition]:
        """Search tools by name/description.

        Simple substring match for MVP.
        Premium: embedding-based semantic search.

        Args:
            query: Search query

        Returns:
            List of matching tools
        """
        query_lower = query.lower()
        results: list[ToolDefinition] = []

        for tool in self._tools.values():
            if query_lower in tool.name.lower() or query_lower in tool.description.lower():
                results.append(tool)

        return results

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Get tools formatted for MCP tools/list response.

        Returns only available tools in MCP format.

        Returns:
            List of MCP tool dicts
        """
        return [tool.to_mcp_tool() for tool in self.list_available()]

    def get_router(self, tool_name: str) -> ToolRouter | None:
        """Get routing info for a tool.

        Returns info needed to invoke the tool:
        - Source type (MCP, system)
        - Server name (for MCP)

        Args:
            tool_name: Name of tool

        Returns:
            ToolRouter or None if tool not found
        """
        tool = self.get(tool_name)
        if tool is None:
            return None
        return ToolRouter(
            source=tool.source,
            server_name=tool.server_name,
        )

    async def on_config_change(self, new_config: ToolsConfig) -> RefreshResult:
        """Handle config change.

        - Update MCP manager
        - Refresh tools

        Args:
            new_config: New tools configuration

        Returns:
            RefreshResult with changes
        """
        self._log(LogLevel.INFO, "Handling config change")

        # Update config
        self._config = new_config

        # Update MCP manager
        await self._mcp_manager.on_config_change(new_config)

        # Re-register system tools (in case config changed)
        self._register_system_tools()

        # Refresh all tools
        result = await self.refresh()

        self._log(LogLevel.INFO, "Config change complete")

        return result

    def _register_system_tools(self) -> None:
        """Register built-in system tools."""
        if self._config.system_tools.python_exec_enabled:
            self._tools["python_exec"] = ToolDefinition(
                name="python_exec",
                description="Execute Python code in secure sandbox",
                source=ToolSource.SYSTEM,
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Execution timeout in seconds",
                        },
                    },
                    "required": ["code"],
                },
                status=ToolStatus.AVAILABLE,
                last_seen=datetime.now(UTC),
            )
