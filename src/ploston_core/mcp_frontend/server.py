"""MCP Frontend - AEL as MCP server."""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from ploston_core.config import MCPHTTPConfig, Mode, ModeManager
from ploston_core.engine import WorkflowEngine
from ploston_core.errors import AELError, create_error
from ploston_core.errors.errors import ErrorCategory
from ploston_core.invoker import ToolInvoker
from ploston_core.logging import AELLogger
from ploston_core.registry import ToolRegistry
from ploston_core.telemetry import ChainDetector
from ploston_core.types import ExecutionStatus, MCPTransport

if TYPE_CHECKING:
    from ploston.workflow import WorkflowRegistry

from .http_transport import HTTPTransport
from .stdio import read_message, write_message
from .types import MCPServerConfig


class MCPFrontend:
    """
    AEL as MCP server.

    Exposes:
    - All registered tools (passthrough)
    - All registered workflows (as workflow_* tools)

    Transport: stdio (default) or HTTP
    """

    def __init__(
        self,
        workflow_engine: WorkflowEngine,
        tool_registry: ToolRegistry,
        workflow_registry: "WorkflowRegistry",
        tool_invoker: ToolInvoker,
        config: MCPServerConfig | None = None,
        logger: AELLogger | None = None,
        mode_manager: ModeManager | None = None,
        config_tool_registry: Any | None = None,
        transport: MCPTransport = MCPTransport.STDIO,
        http_config: MCPHTTPConfig | None = None,
        rest_app: Any | None = None,
        rest_prefix: str = "/api/v1",
        chain_detector: ChainDetector | None = None,
    ):
        """Initialize MCP frontend.

        Args:
            workflow_engine: Workflow engine for executing workflows
            tool_registry: Tool registry for listing tools
            workflow_registry: Workflow registry for listing workflows
            tool_invoker: Tool invoker for executing tools
            config: MCP server configuration
            logger: Logger instance
            mode_manager: Mode manager for tracking configuration/running mode
            config_tool_registry: Registry for config tools (ael:config_*)
            transport: Transport type (stdio or http)
            http_config: HTTP transport configuration (required if transport is http)
            rest_app: Optional FastAPI app to mount for REST API (dual-mode)
            rest_prefix: URL prefix for REST API (default: /api/v1)
            chain_detector: Optional chain detector for detecting tool sequences
        """
        self._workflow_engine = workflow_engine
        self._tool_registry = tool_registry
        self._workflow_registry = workflow_registry
        self._tool_invoker = tool_invoker
        self._config = config or MCPServerConfig()
        self._logger = logger
        self._running = False
        self._transport = transport
        self._http_config = http_config or MCPHTTPConfig()
        self._http_transport: HTTPTransport | None = None
        self._rest_app = rest_app
        self._rest_prefix = rest_prefix

        # Mode management
        self._mode_manager = mode_manager or ModeManager()
        self._config_tool_registry = config_tool_registry

        # Chain detection (T-446)
        self._chain_detector = chain_detector

        # Register for mode change notifications
        self._mode_manager.on_mode_change(self._on_mode_change)

    async def start(self) -> None:
        """Start MCP server.

        For stdio: Reads JSON-RPC messages from stdin, writes to stdout.
        For HTTP: Starts HTTP server with /mcp and /mcp/sse endpoints.
        """
        self._running = True

        if self._transport == MCPTransport.HTTP:
            await self._start_http()
        else:
            await self._start_stdio()

    async def _start_stdio(self) -> None:
        """Start MCP server on stdio transport."""
        while self._running:
            message = await read_message()
            if message is None:
                break

            response = await self._handle_message(message)
            if response:
                await write_message(response)

    async def _start_http(self) -> None:
        """Start MCP server on HTTP transport.

        If rest_app is provided, mounts it for dual-mode operation (MCP + REST API).
        """
        import uvicorn

        self._http_transport = HTTPTransport(
            message_handler=self._handle_message,
            host=self._http_config.host,
            port=self._http_config.port,
            cors_origins=self._http_config.cors_origins,
            tls_enabled=self._http_config.tls.enabled,
            tls_cert_file=self._http_config.tls.cert_file,
            tls_key_file=self._http_config.tls.key_file,
            rest_app=self._rest_app,
            rest_prefix=self._rest_prefix,
        )
        self._http_transport.start()

        config = uvicorn.Config(
            self._http_transport.app,
            host=self._http_config.host,
            port=self._http_config.port,
            log_level="info",
        )

        # Add TLS if enabled
        if self._http_config.tls.enabled:
            config.ssl_certfile = self._http_config.tls.cert_file
            config.ssl_keyfile = self._http_config.tls.key_file

        server = uvicorn.Server(config)
        await server.serve()

    async def stop(self) -> None:
        """Stop MCP server."""
        self._running = False
        if self._http_transport:
            self._http_transport.stop()

    def _on_mode_change(self, new_mode: Mode) -> None:
        """Send tools/list_changed notification when mode changes.

        Args:
            new_mode: The new mode
        """
        asyncio.create_task(self._send_tools_changed_notification())

    async def _send_tools_changed_notification(self) -> None:
        """Send MCP notification that tools list has changed."""
        notification = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        if self._transport == MCPTransport.HTTP and self._http_transport:
            await self._http_transport.send_notification(notification)
        else:
            await write_message(notification)

    async def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Route message to appropriate handler.

        Args:
            message: JSON-RPC message

        Returns:
            JSON-RPC response or None (None for notifications)
        """
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        # JSON-RPC 2.0: Notifications (no id) should not receive a response
        is_notification = "id" not in message

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "ping":
                result = {"pong": True}
            elif method and method.startswith("notifications/"):
                # MCP notifications - no response needed
                return None
            else:
                # Unknown method - only respond if it's a request (has id)
                if is_notification:
                    return None
                return self._error_response(msg_id, -32601, f"Method not found: {method}")

            # Only respond to requests, not notifications
            if is_notification:
                return None
            return self._success_response(msg_id, result)

        except AELError as e:
            if is_notification:
                return None
            return self._error_response(
                msg_id,
                e.http_status,
                e.message,
                {"code": e.code, "detail": e.detail},
            )
        except Exception as e:
            if is_notification:
                return None
            return self._error_response(msg_id, 500, str(e))

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle initialize request.

        Args:
            params: Initialize parameters

        Returns:
            Initialize response
        """
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": self._config.name,
                "version": self._config.version,
            },
            "capabilities": {
                "tools": {"listChanged": True},
            },
        }

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request with mode awareness.

        Args:
            params: List parameters

        Returns:
            Tools list response
        """
        tools = []

        if self._mode_manager.mode == Mode.CONFIGURATION:
            # Configuration mode: only config tools
            if self._config_tool_registry:
                tools = self._config_tool_registry.get_for_mcp_exposure()
        else:
            # Running mode: all tools + workflows + configure
            if self._config.expose_tools:
                for tool in self._tool_registry.get_for_mcp_exposure():
                    tools.append(tool)

            if self._config.expose_workflows:
                for workflow in self._workflow_registry.get_for_mcp_exposure():
                    tools.append(workflow)

            # Add configure for switching back to config mode
            if self._config_tool_registry:
                configure_tool = self._config_tool_registry.get_configure_tool_for_mcp_exposure()
                if configure_tool:
                    tools.append(configure_tool)

        return {"tools": tools}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request with mode awareness.

        Args:
            params: Call parameters

        Returns:
            Tool call response
        """
        name = params.get("name")
        arguments = params.get("arguments", {})

        if not name:
            raise create_error("PARAM_INVALID", message="Tool name is required")

        # Config tools (ael:* namespace)
        if name.startswith("ael:") or name == "configure":
            return await self._handle_config_tool_call(name, arguments)

        # Workflow calls
        if name.startswith("workflow_"):
            if self._mode_manager.mode == Mode.CONFIGURATION:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="Workflows not available in configuration mode. Call config_done first.",
                    tool_name=name,
                    http_status=503,
                )
            workflow_id = name[len("workflow_") :]
            return await self._execute_workflow(workflow_id, arguments)

        # Regular tool calls
        if self._mode_manager.mode == Mode.CONFIGURATION:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message="Tools not available in configuration mode. Call config_done first.",
                tool_name=name,
                http_status=503,
            )
        return await self._execute_tool(name, arguments)

    async def _handle_config_tool_call(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle config tool calls with mode awareness.

        Args:
            name: Tool name (ael:*)
            arguments: Tool arguments

        Returns:
            Tool call response
        """
        if not self._config_tool_registry:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Config tool {name} not available (no config tool registry)",
                tool_name=name,
                http_status=503,
            )

        if self._mode_manager.mode == Mode.CONFIGURATION:
            # In config mode, configure is not available
            if name == "configure":
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="configure only available in running mode",
                    tool_name=name,
                    http_status=503,
                )
            return await self._config_tool_registry.call(name, arguments)
        else:
            # In running mode, only configure is available
            if name == "configure":
                return await self._config_tool_registry.call(name, arguments)
            else:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message=f"{name} only available in configuration mode. Call configure first.",
                    tool_name=name,
                    http_status=503,
                )

    async def _execute_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute workflow and return MCP response.

        Args:
            workflow_id: Workflow ID
            inputs: Workflow inputs

        Returns:
            MCP response
        """
        result = await self._workflow_engine.execute(workflow_id, inputs)

        if result.status == ExecutionStatus.COMPLETED:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result.outputs),
                    }
                ],
                "isError": False,
            }
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": result.error.message if result.error else "Workflow failed",
                    }
                ],
                "isError": True,
            }

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute tool directly and return MCP response.

        Args:
            tool_name: Tool name
            arguments: Tool arguments

        Returns:
            MCP response
        """
        result = await self._tool_invoker.invoke(tool_name, arguments)

        # Chain detection (T-446): Process tool call for chain detection
        # Only for direct tool calls (not workflows)
        if self._chain_detector and result.success and not tool_name.startswith("workflow_"):
            try:
                await self._chain_detector.process_tool_call(
                    tool_name=tool_name,
                    params=arguments,
                    result=result.output,
                )
            except Exception:
                # Chain detection is non-critical - don't fail the tool call
                pass

        if result.success:
            response: dict[str, Any] = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            json.dumps(result.output)
                            if not isinstance(result.output, str)
                            else result.output
                        ),
                    }
                ],
                "isError": False,
            }
            # Include structuredContent if available (required by MCP spec when outputSchema is defined)
            if result.structured_content is not None:
                response["structuredContent"] = result.structured_content
            return response
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": result.error.message if result.error else "Tool call failed",
                    }
                ],
                "isError": True,
            }

    def _success_response(self, msg_id: Any, result: Any) -> dict[str, Any]:
        """Build success response.

        Args:
            msg_id: Message ID
            result: Result data

        Returns:
            JSON-RPC success response
        """
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    def _error_response(
        self,
        msg_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> dict[str, Any]:
        """Build error response.

        Args:
            msg_id: Message ID
            code: Error code
            message: Error message
            data: Additional error data

        Returns:
            JSON-RPC error response
        """
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": error,
        }
