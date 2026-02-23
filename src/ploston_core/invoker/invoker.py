"""Tool invoker implementation."""

import time
from typing import TYPE_CHECKING, Any

from ploston_core.errors import create_error
from ploston_core.sandbox import ToolCallerProtocol
from ploston_core.telemetry import instrument_tool_call, record_tool_result
from ploston_core.telemetry.metrics import MetricLabels
from ploston_core.types import LogLevel, ToolSource, ToolStatus

from .types import ToolCallResult

# Map ToolSource enum to metric source labels
_SOURCE_TO_METRIC_LABEL = {
    ToolSource.MCP: MetricLabels.SOURCE_CONFIGURED,  # MCP servers = "configured"
    ToolSource.NATIVE: MetricLabels.SOURCE_NATIVE,  # Native tools = "native"
    ToolSource.SYSTEM: MetricLabels.SOURCE_SYSTEM,  # System tools = "system"
    # Note: RUNNER source is handled separately via tool name prefix
}

if TYPE_CHECKING:
    from ploston_core.errors import ErrorFactory
    from ploston_core.logging import AELLogger
    from ploston_core.mcp import MCPClientManager
    from ploston_core.registry import ToolRegistry

    from .factory import SandboxFactory


class ToolInvoker(ToolCallerProtocol):
    """Unified interface for tool invocation.

    Routes calls to appropriate backend:
    - MCP tools → MCP Client Manager
    - System tools (python_exec) → Python Exec Sandbox

    Implements ToolCallerProtocol so sandbox can use it for
    nested tool calls without circular dependency.
    """

    def __init__(
        self,
        tool_registry: "ToolRegistry",
        mcp_manager: "MCPClientManager",
        sandbox_factory: "SandboxFactory",
        logger: "AELLogger | None" = None,
        error_factory: "ErrorFactory | None" = None,
    ):
        """Initialize tool invoker.

        Args:
            tool_registry: Tool registry for routing
            mcp_manager: MCP client manager for MCP tools
            sandbox_factory: Factory for creating sandboxes
            logger: Optional logger
            error_factory: Optional error factory
        """
        self._registry = tool_registry
        self._mcp_manager = mcp_manager
        self._sandbox_factory = sandbox_factory
        self._logger = logger
        self._error_factory = error_factory

    # ─────────────────────────────────────────────────────────────
    # ToolCallerProtocol implementation
    # ─────────────────────────────────────────────────────────────

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Call a tool (ToolCallerProtocol implementation).

        Used by sandbox for nested tool calls.
        Returns just the output value (not full ToolCallResult).

        Args:
            tool_name: Name of tool to call
            params: Tool parameters

        Returns:
            Tool output

        Raises:
            AELError if tool call fails
        """
        result = await self.invoke(tool_name, params)
        if not result.success:
            raise result.error
        return result.output

    # ─────────────────────────────────────────────────────────────
    # Main interface
    # ─────────────────────────────────────────────────────────────

    def _get_source_label(self, tool_name: str, tool_source: ToolSource) -> str:
        """Get the metric source label for a tool.

        Args:
            tool_name: Tool name (may have runner prefix)
            tool_source: Tool source enum

        Returns:
            Source label for metrics (native, local, system, configured)
        """
        # Check for runner prefix (e.g., "runner__mcp__tool_name")
        if tool_name.startswith("runner__") or "__runner__" in tool_name:
            return MetricLabels.SOURCE_LOCAL

        # Map ToolSource to metric label
        return _SOURCE_TO_METRIC_LABEL.get(tool_source, MetricLabels.SOURCE_CONFIGURED)

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        timeout_seconds: int | None = None,
        step_id: str | None = None,
        execution_id: str | None = None,
    ) -> ToolCallResult:
        """Invoke a tool.

        Args:
            tool_name: Name of tool to invoke
            params: Tool parameters
            timeout_seconds: Optional timeout override in seconds
            step_id: For logging context
            execution_id: For logging context

        Returns:
            ToolCallResult with output or error

        Raises:
            AELError(TOOL_UNAVAILABLE) if tool not found
            AELError(TOOL_TIMEOUT) if call times out
            AELError(TOOL_FAILED) if tool returns error
        """
        # 1. Get tool from registry first to determine source for telemetry
        tool = self._registry.get_or_raise(tool_name)

        # 2. Get routing info to determine source
        router = self._registry.get_router(tool_name)
        if router is None:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                reason="No routing information available for tool",
            )

        # 3. Determine source label for metrics
        source_label = self._get_source_label(tool_name, router.source)

        # Instrument tool invocation with telemetry (including source)
        async with instrument_tool_call(tool_name, source=source_label) as telemetry_result:
            # 4. Check availability
            if tool.status != ToolStatus.AVAILABLE:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    tool_name=tool_name,
                    reason="Tool is currently unavailable",
                )

            # 5. Log invocation
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "invoker",
                    f"Invoking tool: {tool_name}",
                    {
                        "tool_name": tool_name,
                        "source": source_label,
                        "step_id": step_id,
                        "execution_id": execution_id,
                    },
                )

            # 6. Route to appropriate backend
            if router.source == ToolSource.MCP:
                if router.server_name is None:
                    raise create_error(
                        "INTERNAL_ERROR",
                        message=f"MCP tool {tool_name} has no server_name",
                    )
                result = await self._invoke_mcp(
                    tool_name, params, router.server_name, timeout_seconds
                )
            elif router.source == ToolSource.SYSTEM:
                result = await self._invoke_system(tool_name, params, timeout_seconds)
            else:
                raise create_error(
                    "INTERNAL_ERROR",
                    message=f"Unknown tool source: {router.source}",
                )

            # Record telemetry result
            record_tool_result(
                telemetry_result,
                success=result.success,
                error_code=type(result.error).__name__ if result.error else None,
            )
            return result

    async def _invoke_mcp(
        self,
        tool_name: str,
        params: dict[str, Any],
        server_name: str,
        timeout_seconds: int | None = None,
    ) -> ToolCallResult:
        """Invoke tool via MCP server.

        Args:
            tool_name: Name of tool to invoke
            params: Tool parameters
            server_name: MCP server name
            timeout_seconds: Optional timeout in seconds

        Returns:
            ToolCallResult with output or error
        """
        start = time.time()

        try:
            # MCPCallResult from MCP Client Manager
            mcp_result = await self._mcp_manager.call_tool(
                server_name=server_name,
                tool_name=tool_name,
                arguments=params,
                timeout_seconds=timeout_seconds,
            )

            duration_ms = int((time.time() - start) * 1000)

            if mcp_result.is_error:
                # ErrorFactory doesn't have from_mcp_error, just create error directly
                error = create_error(
                    "TOOL_FAILED",
                    tool_name=tool_name,
                    message=str(mcp_result.error),
                )

                return ToolCallResult(
                    success=False,
                    output=None,
                    duration_ms=duration_ms,
                    tool_name=tool_name,
                    error=error,
                )

            return ToolCallResult(
                success=True,
                output=mcp_result.content,
                duration_ms=duration_ms,
                tool_name=tool_name,
                structured_content=mcp_result.structured_content,
            )

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            error = create_error(
                "TOOL_FAILED",
                tool_name=tool_name,
                message=str(e),
            )

            return ToolCallResult(
                success=False,
                output=None,
                duration_ms=duration_ms,
                tool_name=tool_name,
                error=error,
            )

    async def _invoke_system(
        self,
        tool_name: str,
        params: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> ToolCallResult:
        """Invoke system tool (python_exec).

        Args:
            tool_name: Name of system tool
            params: Tool parameters
            timeout_seconds: Optional timeout in seconds

        Returns:
            ToolCallResult with output or error
        """
        if tool_name == "python_exec":
            return await self._invoke_python_exec(params, timeout_seconds)
        else:
            raise create_error(
                "INTERNAL_ERROR",
                message=f"Unknown system tool: {tool_name}",
            )

    async def _invoke_python_exec(
        self,
        params: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> ToolCallResult:
        """Invoke python_exec system tool.

        Args:
            params: Tool parameters (must include 'code')
            timeout_seconds: Optional timeout in seconds

        Returns:
            ToolCallResult with output or error
        """
        code = params.get("code")
        if not code:
            raise create_error(
                "PARAM_INVALID",
                tool_name="python_exec",
                message="'code' parameter is required",
            )

        # Create sandbox instance via factory
        sandbox = self._sandbox_factory.create()

        # PythonExecSandbox.execute takes (code, context) where context is dict[str, Any]
        # SandboxResult has: success, result, stdout, stderr, execution_time, error, tool_call_count
        # NOTE: Spec says execute() should accept SandboxContext, but MVP implementation uses dict.
        # Convert SandboxContext to dict if needed (see MVP_SPEC_DEVIATIONS.md)
        context_param = params.get("context", {})
        # It's a SandboxContext object if it has 'inputs' attribute
        context = {"context": context_param} if hasattr(context_param, "inputs") else context_param
        result = await sandbox.execute(code, context=context)

        # Convert execution_time (seconds) to duration_ms (milliseconds)
        duration_ms = int(result.execution_time * 1000)

        # Convert error string to AELError if present
        error = None
        if result.error:
            error = create_error(
                "CODE_RUNTIME",
                message=result.error,
            )

        return ToolCallResult(
            success=result.success,
            output=result.result,  # SandboxResult uses 'result' not 'output'
            duration_ms=duration_ms,
            tool_name="python_exec",
            error=error,
        )
