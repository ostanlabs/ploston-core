"""MCP Frontend - AEL as MCP server."""

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from ploston_core.api.routers import is_runner_connected, send_tool_call_to_runner
from ploston_core.config import MCPHTTPConfig, Mode, ModeManager
from ploston_core.engine import WorkflowEngine
from ploston_core.errors import AELError, create_error
from ploston_core.errors.errors import ErrorCategory
from ploston_core.invoker import ToolInvoker
from ploston_core.logging import AELLogger
from ploston_core.mcp_frontend.http_transport import bridge_context
from ploston_core.registry import ToolRegistry
from ploston_core.runner_management.registry import RunnerRegistry
from ploston_core.runner_management.router import parse_tool_prefix
from ploston_core.telemetry import ChainDetector, instrument_tool_call, record_tool_result
from ploston_core.telemetry.context import direct_execution_id, direct_session_id
from ploston_core.telemetry.store.types import (
    ErrorRecord as TelemetryErrorRecord,
)
from ploston_core.telemetry.store.types import (
    ExecutionStatus as TelemetryExecutionStatus,
)
from ploston_core.telemetry.store.types import (
    ExecutionType,
    ToolCallSource,
)
from ploston_core.telemetry.store.wrappers import (
    record_tool_call,
    synthetic_direct_step,
)
from ploston_core.types import ExecutionStatus, LogLevel, MCPTransport

if TYPE_CHECKING:
    from ploston_core.workflow import WorkflowRegistry

from .http_transport import HTTPTransport
from .stdio import read_message, write_message
from .types import MCPServerConfig

logger = logging.getLogger(__name__)


def _split_tool_name(qualified_name: str) -> tuple[str, str]:
    """Split a qualified tool name into (bridge, tool_name).

    ``obsidian-mcp__read_docs`` → ``("obsidian-mcp", "read_docs")``

    If the name does not contain ``__``, *bridge* is returned as an empty
    string and *tool_name* is the original name.
    """
    if "__" in qualified_name:
        bridge, _, tool = qualified_name.partition("__")
        return bridge, tool
    return "", qualified_name


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
        runner_registry: RunnerRegistry | None = None,
        workflow_tools_provider: Any | None = None,
        telemetry_collector: Any | None = None,
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
            runner_registry: Optional runner registry for routing tools to runners (DEC-123)
            workflow_tools_provider: Optional WorkflowToolsProvider for workflow CRUD tools
            telemetry_collector: Optional TelemetryCollector for execution lifecycle (DEC-152)
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

        # Runner routing (DEC-123)
        self._runner_registry = runner_registry

        # Workflow CRUD tools
        self._workflow_tools_provider = workflow_tools_provider

        # Telemetry collector for execution lifecycle (Tier 3 — DEC-152)
        self._telemetry_collector = telemetry_collector

        # Strong references to fire-and-forget background tasks.  asyncio only
        # keeps a weak reference to tasks created via ``create_task``; without a
        # strong reference the task can be garbage-collected mid-run and
        # silently cancelled.  A done-callback discards each task when complete.
        self._background_tasks: set[asyncio.Task[Any]] = set()

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
        task = asyncio.create_task(self._send_tools_changed_notification())
        # Retain a strong reference so the task is not GC'd mid-flight, and
        # discard it once finished to avoid unbounded growth.
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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
            # MCP spec: tool execution errors are reported inside
            # ``result`` with ``isError: true``, NOT as JSON-RPC
            # protocol errors.  Protocol errors (``{"error": ...}``)
            # are reserved for infrastructure failures (unknown method,
            # invalid JSON, server crash).  Returning a JSON-RPC error
            # for a business-logic failure (bad input, not found, …)
            # causes some MCP clients to surface "Tool execution failed"
            # without relaying the structured payload to the LLM.
            if method == "tools/call":
                error_payload: dict[str, Any] = {
                    "code": e.code,
                    "message": e.message,
                }
                if e.detail:
                    error_payload["detail"] = e.detail
                if e.suggestion:
                    error_payload["suggestion"] = e.suggestion
                if e.data:
                    error_payload["data"] = e.data
                error_payload["retryable"] = e.retryable
                return self._success_response(
                    msg_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(error_payload)},
                        ],
                        "isError": True,
                    },
                )
            # Non-tools/call methods: keep JSON-RPC protocol error.
            error_data: dict[str, Any] = {"code": e.code}
            if e.detail:
                error_data["detail"] = e.detail
            if e.suggestion:
                error_data["suggestion"] = e.suggestion
            if e.data:
                error_data["data"] = e.data
            return self._error_response(
                msg_id,
                e.http_status,
                e.message,
                error_data,
            )
        except Exception as e:
            if is_notification:
                return None
            # Log full traceback server-side for debugging
            logger.exception("Unhandled exception in MCP message handler")
            import traceback

            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            short_tb = "".join(tb_lines[-3:]).strip()  # last 3 frames
            # MCP spec: same principle — tool-call unhandled exceptions
            # should be ``isError: true`` inside ``result``.
            if method == "tools/call":
                error_payload_exc: dict[str, Any] = {
                    "code": "INTERNAL_ERROR",
                    "message": f"Internal server error: {type(e).__name__}: {e}",
                    "detail": (
                        f"An unexpected error occurred while processing the request. "
                        f"Error type: {type(e).__name__}, message: {e}"
                    ),
                    "traceback_tail": short_tb,
                    "retryable": False,
                }
                return self._success_response(
                    msg_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(error_payload_exc)},
                        ],
                        "isError": True,
                    },
                )
            return self._error_response(
                msg_id,
                500,
                f"Internal server error: {type(e).__name__}: {e}",
                {
                    "code": "INTERNAL_ERROR",
                    "detail": f"An unexpected error occurred while processing the request. "
                    f"Error type: {type(e).__name__}, message: {e}",
                    "traceback_tail": short_tb,
                },
            )

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
        """Handle tools/list request with unified tag filtering (DEC-170).

        Supports optional filtering via params:
        - tags: list of tags to filter by (match-all semantics)
        - sources: legacy list of sources (deprecated, mapped to tags internally)

        Args:
            params: List parameters

        Returns:
            Tools list response
        """
        tools: list[dict[str, Any]] = []

        # Parse tag filter from params (DEC-170)
        tag_filter: set[str] | None = None
        if params:
            raw_tags = params.get("tags")
            if raw_tags:
                tag_filter = set(raw_tags)
            else:
                # Legacy source filter → map to tags
                source_filter = params.get("sources")
                if source_filter:
                    tag_filter = set()
                    for src in source_filter:
                        if src in ("mcp", "native", "system"):
                            tag_filter.add(f"source:{src}")
                        elif src == "runner":
                            tag_filter.add("source:runner")

        if self._mode_manager.mode == Mode.CONFIGURATION:
            # Configuration mode: only config tools
            if self._config_tool_registry:
                tools = self._config_tool_registry.get_for_mcp_exposure()
        else:
            # Running mode: aggregate all tool sources, attach _ploston_tags,
            # then filter and strip before returning.

            # 1. CP tools from ToolRegistry
            if self._config.expose_tools:
                for tool in self._tool_registry.get_for_mcp_exposure():
                    # Inject _ploston_tags from ToolDefinition
                    tool_def = self._tool_registry.get(tool["name"])
                    if tool_def:
                        tool["_ploston_tags"] = set(tool_def.tags)
                    tools.append(tool)

            # 2. Workflow execution tools (bare-name)
            if self._config.expose_workflows:
                for workflow in self._workflow_registry.get_for_mcp_exposure():
                    tools.append(workflow)

            # 3. Configure tool
            if self._config_tool_registry:
                configure_tool = self._config_tool_registry.get_configure_tool_for_mcp_exposure()
                if configure_tool:
                    configure_tool["_ploston_tags"] = {"kind:config"}
                    tools.append(configure_tool)

            # 4. Workflow management tools
            if self._workflow_tools_provider:
                for tool in self._workflow_tools_provider.get_for_mcp_exposure():
                    tools.append(tool)

            # 5. Runner tools (DEC-123)
            if self._runner_registry:
                for runner in self._runner_registry.list():
                    if runner.status.value == "connected" and runner.available_tools:
                        for tool_info in runner.available_tools:
                            if isinstance(tool_info, str):
                                tool_name = tool_info
                                tool_desc = f"Tool from runner '{runner.name}'"
                                tool_schema: dict[str, Any] = {}
                            else:
                                tool_name = tool_info.get("name", str(tool_info))
                                tool_desc = tool_info.get(
                                    "description", f"Tool from runner '{runner.name}'"
                                )
                                tool_schema = tool_info.get("inputSchema", {})

                            prefixed_name = f"{runner.name}__{tool_name}"
                            tools.append(
                                {
                                    "name": prefixed_name,
                                    "description": tool_desc,
                                    "inputSchema": tool_schema,
                                    "_ploston_tags": {"source:runner", f"runner:{runner.name}"},
                                }
                            )

            # Pre-serialization tag filter (DEC-170 §2.6)
            if tag_filter:
                tools = [t for t in tools if tag_filter.issubset(t.get("_ploston_tags", set()))]

        # Strip _ploston_tags before MCP serialization
        for tool in tools:
            tool.pop("_ploston_tags", None)

        return {"tools": tools}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call with 6-step unified routing (DEC-169/170).

        Routing order:
        1. Config namespace (ael:*, ploston:*, configure)
        2. Runner prefix (runner__mcp__tool)
        3. Management tools (workflow_schema, workflow_list, …, workflow_run)
        4. CP tool registry (exact name match)
        5. Workflow registry (bare-name match)
        6. TOOL_UNAVAILABLE

        Args:
            params: Call parameters

        Returns:
            Tool call response
        """
        name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            logger.info(
                f"[trace] cp<-bridge name={name} arguments={json.dumps(arguments, default=str)}"
            )
        except Exception:
            pass

        if not name:
            raise create_error("PARAM_INVALID", message="Tool name is required")

        # ── Step 1: Config namespace ──
        if name.startswith("ael:") or name.startswith("ploston:") or name == "configure":
            return await self._handle_config_tool_call(name, arguments)

        # ── Step 2: Runner prefix (DEC-123: runner__mcp__tool) ──
        runner_name, mcp_name, actual_tool = parse_tool_prefix(name)
        if runner_name:
            tool_for_runner = f"{mcp_name}__{actual_tool}" if mcp_name else actual_tool
            return await self._execute_runner_tool(runner_name, tool_for_runner, arguments)

        # ── Step 3: Workflow management tools ──
        from ploston_core.workflow.tools import WorkflowToolsProvider

        if WorkflowToolsProvider.is_mgmt_tool(name):
            if self._mode_manager.mode == Mode.CONFIGURATION:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="Workflows not available in configuration mode. Call config_done first.",
                    tool_name=name,
                    http_status=503,
                )
            if self._workflow_tools_provider:
                return await self._execute_workflow_mgmt_tool(name, arguments)

        # ── Step 4: CP tool registry (exact name) ──
        if self._tool_registry.get(name):
            if self._mode_manager.mode == Mode.CONFIGURATION:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="Tools not available in configuration mode. Call config_done first.",
                    tool_name=name,
                    http_status=503,
                )
            return await self._execute_tool(name, arguments)

        # ── Step 5: Workflow registry (bare-name) ──
        if self._workflow_registry.get(name):
            if self._mode_manager.mode == Mode.CONFIGURATION:
                raise AELError(
                    code="TOOL_UNAVAILABLE",
                    category=ErrorCategory.TOOL,
                    message="Workflows not available in configuration mode. Call config_done first.",
                    tool_name=name,
                    http_status=503,
                )
            return await self._execute_workflow(name, arguments)

        # ── Step 6: Not found ──
        raise AELError(
            code="TOOL_UNAVAILABLE",
            category=ErrorCategory.TOOL,
            message=f"Tool '{name}' not found in any registry.",
            tool_name=name,
            http_status=404,
        )

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
            # In running mode, only configure is available from config tools
            _running_mode_tools = {"configure", "ploston:configure"}
            if name in _running_mode_tools:
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
        """Execute a registered workflow (bare-name MCP tool) with full telemetry.

        Produces a single ``source='direct'`` row in ``tool_calls`` for the
        workflow tool itself (e.g. ``ostanlabs_cascade_diagnose``), exactly
        like ``_execute_tool`` does for regular tools.  The execution_id is
        forwarded to the engine as ``parent_execution_id`` so inner
        step/tool_call rows share the same execution — but those inner
        rows keep their ``tool_step``/``code_block`` source, allowing
        the Session Inspector to show only the opaque wrapper line
        (``source='direct'``) while the Workflow Execution Logs dashboard
        shows the inner detail.

        Args:
            workflow_id: Workflow ID (bare name, e.g. ``ostanlabs_cascade_diagnose``)
            inputs: Workflow inputs

        Returns:
            MCP response
        """
        execution_id: str | None = None
        ctx_token = None
        sess_token = None

        # DEC-145: capture bridge session from ContextVar set by HTTPTransport (F-061)
        _bctx = bridge_context.get(None)
        bridge_session_id = _bctx.bridge_id if _bctx else None
        _sess_for_logger = _bctx.session_id if _bctx else None
        _bridge_id = _bctx.bridge_id if _bctx else None
        if _sess_for_logger:
            sess_token = direct_session_id.set(_sess_for_logger)

        # Start telemetry execution record
        if self._telemetry_collector:
            try:
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=workflow_id,
                    source="mcp",
                    session_id=_sess_for_logger,
                    bridge_session_id=_bridge_id,
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        try:
            start_ms = int(time.time() * 1000)

            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "direct",
                    f"Direct workflow call: {workflow_id}",
                    {
                        "source": "workflow",
                        "event": "direct_tool_called",
                        "tool_name": workflow_id,
                    },
                )

            # Wrap with synthetic_direct_step + record_tool_call to produce
            # the single source='direct' row in tool_calls, matching what
            # _execute_tool does for regular tools.
            response: dict[str, Any] = {}
            invoke_error: BaseException | None = None
            async with synthetic_direct_step(
                self._telemetry_collector,
                execution_id=execution_id,
                tool_name=workflow_id,
            ) as _step_id:
                async with record_tool_call(
                    self._telemetry_collector,
                    execution_id=execution_id,
                    step_id=_step_id,
                    tool_name=workflow_id,
                    params=inputs,
                    source=ToolCallSource.DIRECT,
                    bridge_id=_bridge_id,
                    session_id=_sess_for_logger,
                ) as _call_handle:
                    try:
                        result = await self._workflow_engine.execute(
                            workflow_id,
                            inputs,
                            bridge_session_id=bridge_session_id,
                            parent_execution_id=execution_id,
                        )
                    except Exception as _exc:
                        invoke_error = _exc
                        _call_handle.set_error(_exc)
                        raise

                    success = result.status == ExecutionStatus.COMPLETED
                    if success:
                        response = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result.outputs),
                                }
                            ],
                            "isError": False,
                        }
                        _call_handle.set_result(result.outputs)
                    else:
                        err_msg = result.error.message if result.error else "Workflow failed"
                        response = {
                            "content": [{"type": "text", "text": err_msg}],
                            "isError": True,
                        }
                        _call_handle.set_error(
                            TelemetryErrorRecord(
                                code=getattr(result.error, "code", "WORKFLOW_FAILED"),
                                category="workflow",
                                message=err_msg,
                            )
                        )

            duration_ms = int(time.time() * 1000) - start_ms

            if self._logger:
                level = LogLevel.INFO if success else LogLevel.ERROR
                event = "direct_tool_completed" if success else "direct_tool_failed"
                self._logger._log(
                    level,
                    "direct",
                    f"Direct workflow {'completed' if success else 'failed'} "
                    f"({duration_ms}ms): {workflow_id}",
                    {
                        "source": "workflow",
                        "event": event,
                        "tool_name": workflow_id,
                        "duration_ms": duration_ms,
                    },
                )

            # Close telemetry execution record
            if self._telemetry_collector and execution_id:
                try:
                    if success:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.COMPLETED,
                        )
                    else:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code=getattr(result.error, "code", "WORKFLOW_FAILED"),
                                category="workflow",
                                message=(
                                    result.error.message if result.error else "Workflow failed"
                                ),
                            ),
                        )
                except Exception:
                    pass

            return response
        except Exception as exc:
            if self._telemetry_collector and execution_id and invoke_error is not None:
                try:
                    await self._telemetry_collector.end_execution(
                        execution_id=execution_id,
                        status=TelemetryExecutionStatus.FAILED,
                        error=TelemetryErrorRecord(
                            code=getattr(exc, "code", "INTERNAL"),
                            category="workflow",
                            message=str(exc),
                        ),
                    )
                except Exception:
                    pass
            raise
        finally:
            if ctx_token is not None:
                direct_execution_id.reset(ctx_token)
            if sess_token is not None:
                direct_session_id.reset(sess_token)

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
        execution_id: str | None = None
        ctx_token = None
        sess_token = None

        # Bind session_id ContextVar so AELLogger emissions in this request
        # scope carry ael_session_id (S-304/M-082) for the events panel.
        _bctx = bridge_context.get()
        _sess_for_logger = _bctx.session_id if _bctx else None
        if _sess_for_logger:
            sess_token = direct_session_id.set(_sess_for_logger)

        # Start telemetry record for direct tool call (DEC-050 / DEC-152)
        if self._telemetry_collector:
            try:
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=tool_name,
                    source="mcp",
                    session_id=_sess_for_logger,
                    bridge_session_id=_bctx.bridge_id if _bctx else None,  # DEC-145
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        try:
            start_ms = int(time.time() * 1000)
            bridge, short_tool = _split_tool_name(tool_name)

            # START event
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "direct",
                    f"Direct tool call: {tool_name}",
                    {
                        "source": "tool",
                        "event": "direct_tool_called",
                        "tool_name": short_tool,
                        "bridge": bridge,
                    },
                )

            # S-304 / G4 — wrap invocation with synthetic_direct_step + record_tool_call
            _bctx_call = bridge_context.get()
            _bridge_id = _bctx_call.bridge_id if _bctx_call else None
            _session_id = _bctx_call.session_id if _bctx_call else None
            _runner_id = getattr(_bctx_call, "runner_name", None) if _bctx_call else None
            async with synthetic_direct_step(
                self._telemetry_collector,
                execution_id=execution_id,
                tool_name=tool_name,
            ) as _step_id:
                async with record_tool_call(
                    self._telemetry_collector,
                    execution_id=execution_id,
                    step_id=_step_id,
                    tool_name=tool_name,
                    params=arguments,
                    source=ToolCallSource.DIRECT,
                    runner_id=_runner_id,
                    bridge_id=_bridge_id,
                    session_id=_session_id,
                ) as _call_handle:
                    result = await self._tool_invoker.invoke(tool_name, arguments)
                    if result.success:
                        _call_handle.set_result(result.output)
                    elif result.error is not None:
                        _call_handle.set_error(
                            TelemetryErrorRecord(
                                code=getattr(result.error, "code", "UNKNOWN"),
                                category="tool",
                                message=getattr(result.error, "message", str(result.error)),
                            )
                        )
            duration_ms = int(time.time() * 1000) - start_ms

            # RESULT events
            if self._logger:
                if result.success:
                    self._logger._log(
                        LogLevel.INFO,
                        "direct",
                        f"Direct tool completed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_completed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                        },
                    )
                else:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                            "error": result.error.message if result.error else "unknown",
                            "error_type": result.error.code if result.error else "UNKNOWN",
                        },
                    )

            # Chain detection (T-446 / DEC-057): Process tool call for chain detection
            # Only for direct tool calls (not workflows)
            _chain_log = logging.getLogger("ploston.chain_detection")
            if self._chain_detector and result.success and not tool_name.startswith("workflow_"):
                try:
                    _bctx_chain = bridge_context.get()
                    _http_bridge_id = _bctx_chain.bridge_id if _bctx_chain else None
                    _http_session_id = _bctx_chain.session_id if _bctx_chain else None
                    # Tier-4 trackers require a non-None session_id to record
                    # sequence/temporal data.  Prefer the per-conversation session
                    # id; fall back to bridge_id or bridge name when absent.
                    _session = _http_session_id or _http_bridge_id or bridge or "local"
                    _chain_log.debug(
                        "_execute_tool: invoking chain detection tool=%s session=%s bridge_id=%s",
                        tool_name,
                        _session,
                        bridge or _http_bridge_id,
                    )
                    predecessors = await self._chain_detector.process_tool_call(
                        tool_name=tool_name,
                        params=arguments,
                        result=result.output,
                        bridge_id=bridge or _http_bridge_id or None,
                        session_id=_session,
                    )
                    if predecessors:
                        _chain_log.info(
                            "_execute_tool: chain links detected tool=%s predecessors=%s",
                            tool_name,
                            predecessors,
                        )
                except Exception as _chain_err:
                    # Chain detection is non-critical - don't fail the tool call
                    _chain_log.warning(
                        "_execute_tool: chain detection error tool=%s: %s",
                        tool_name,
                        _chain_err,
                    )
            elif not self._chain_detector:
                _chain_log.debug(
                    "_execute_tool: chain_detector is None, skipping for tool=%s",
                    tool_name,
                )
            elif not result.success:
                _chain_log.debug(
                    "_execute_tool: tool call failed, skipping chain detection for tool=%s",
                    tool_name,
                )

            # End telemetry record (DEC-152)
            if self._telemetry_collector and execution_id:
                try:
                    if result.success:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.COMPLETED,
                            outputs={"output": str(result.output)[:500]} if result.output else None,
                        )
                    else:
                        _cp_err = result.error
                        _cp_err_parts = [_cp_err.message if _cp_err else "unknown"]
                        if _cp_err and getattr(_cp_err, "detail", None):
                            _cp_err_parts.append(str(_cp_err.detail))
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code=_cp_err.code if _cp_err else "UNKNOWN",
                                category="tool",
                                message=" — ".join(_cp_err_parts),
                            ),
                        )
                except Exception:
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
        finally:
            if ctx_token is not None:
                direct_execution_id.reset(ctx_token)
            if sess_token is not None:
                direct_session_id.reset(sess_token)

    async def _execute_workflow_mgmt_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a workflow management tool with full telemetry scaffolding.

        Mirrors ``_execute_tool``'s lifecycle (start_execution, ContextVar
        binding, synthetic_direct_step + record_tool_call, end_execution)
        so workflow-mgmt invocations show up in ``executions`` and
        ``tool_calls`` like any other direct tool. Without this wrapper,
        the workflow-mgmt dispatch path (``_handle_tool_call`` Step 3)
        bypasses ``_execute_tool`` entirely and writes nothing to the
        telemetry store — including the inner ``record_tool_call`` inside
        ``_handle_call_tool``, which no-ops when ``direct_execution_id``
        is unset.

        Telemetry tool names are prefixed with ``ploston-authoring__`` so
        dashboard SQL that splits on ``__`` can extract ``mcp_server``.
        Dispatcher tools (``workflow_call_tool``, ``workflow_run``) use
        ``ToolCallSource.WRAPPER`` so they are excluded from session
        dashboard aggregations (token counts, tool call counts) — the
        inner tool calls they dispatch record their own DIRECT rows.

        Success is inferred from the MCP-format response: ``isError``
        must be ``False`` for COMPLETED, otherwise FAILED.
        """
        from ploston_core.workflow.tools import WORKFLOW_DISPATCHER_TOOL_NAMES

        execution_id: str | None = None
        ctx_token = None
        sess_token = None

        _bctx = bridge_context.get()
        _sess_for_logger = _bctx.session_id if _bctx else None
        _bridge_id = _bctx.bridge_id if _bctx else None
        if _sess_for_logger:
            sess_token = direct_session_id.set(_sess_for_logger)

        # Prefix tool name for telemetry so dashboard __-splitting derives
        # mcp_server = "ploston-authoring".  The bare name is still used for
        # the actual provider.call() invocation.
        _telemetry_tool_name = f"ploston-authoring__{tool_name}"
        _is_dispatcher = tool_name in WORKFLOW_DISPATCHER_TOOL_NAMES
        _source = ToolCallSource.WRAPPER if _is_dispatcher else ToolCallSource.DIRECT

        if self._telemetry_collector:
            try:
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=_telemetry_tool_name,
                    source="mcp",
                    session_id=_sess_for_logger,
                    bridge_session_id=_bridge_id,
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        try:
            start_ms = int(time.time() * 1000)
            bridge, short_tool = _split_tool_name(_telemetry_tool_name)

            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "direct",
                    f"Direct tool call: {tool_name}",
                    {
                        "source": "tool",
                        "event": "direct_tool_called",
                        "tool_name": short_tool,
                        "bridge": bridge,
                    },
                )

            response: dict[str, Any] = {}
            invoke_error: BaseException | None = None
            async with synthetic_direct_step(
                self._telemetry_collector,
                execution_id=execution_id,
                tool_name=_telemetry_tool_name,
            ) as _step_id:
                async with record_tool_call(
                    self._telemetry_collector,
                    execution_id=execution_id,
                    step_id=_step_id,
                    tool_name=_telemetry_tool_name,
                    params=arguments,
                    source=_source,
                    bridge_id=_bridge_id,
                    session_id=_sess_for_logger,
                ) as _call_handle:
                    try:
                        response = await self._workflow_tools_provider.call(tool_name, arguments)
                    except Exception as _exc:
                        invoke_error = _exc
                        _call_handle.set_error(_exc)
                        raise
                    if response.get("isError"):
                        _err_text = ""
                        _err_code = "TOOL_ERROR"
                        for _item in response.get("content") or []:
                            if isinstance(_item, dict) and _item.get("type") == "text":
                                _err_text = str(_item.get("text") or "")
                                break
                        # The content text may be a JSON-serialised error
                        # payload (e.g. from workflow_patch).  Try to
                        # extract structured fields for a richer telemetry
                        # message.
                        if _err_text:
                            try:
                                _parsed = json.loads(_err_text)
                                if isinstance(_parsed, dict):
                                    _err_code = str(
                                        _parsed.get("code")
                                        or _parsed.get("error_code")
                                        or _err_code
                                    )
                                    _detail = _parsed.get("detail") or _parsed.get("message") or ""
                                    if _detail:
                                        _err_text = str(_detail)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        _call_handle.set_error(
                            TelemetryErrorRecord(
                                code=_err_code,
                                category="tool",
                                message=_err_text or "workflow management tool failed",
                            )
                        )
                    else:
                        # Store the full response for all workflow mgmt tools.
                        # For dispatchers (wrapper), this captures the actual
                        # agent-facing payload so response_bytes reflects real
                        # token cost.  Inner (wrapped) calls record their own
                        # rows separately — dashboard SQL handles the
                        # accounting: tokens from wrapper+direct, call counts
                        # from wrapped+direct.
                        _call_handle.set_result(response.get("structuredContent") or response)

            duration_ms = int(time.time() * 1000) - start_ms
            success = not response.get("isError", False)

            if self._logger:
                if success:
                    self._logger._log(
                        LogLevel.INFO,
                        "direct",
                        f"Direct tool completed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_completed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                        },
                    )
                else:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "duration_ms": duration_ms,
                        },
                    )

            if self._telemetry_collector and execution_id:
                try:
                    if success:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.COMPLETED,
                        )
                    else:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code="TOOL_ERROR",
                                category="tool",
                                message="workflow management tool failed",
                            ),
                        )
                except Exception:
                    pass

            return response
        except Exception:
            if self._telemetry_collector and execution_id and invoke_error is not None:
                try:
                    await self._telemetry_collector.end_execution(
                        execution_id=execution_id,
                        status=TelemetryExecutionStatus.FAILED,
                        error=TelemetryErrorRecord(
                            code=getattr(invoke_error, "code", "INTERNAL"),
                            category="tool",
                            message=str(invoke_error),
                        ),
                    )
                except Exception:
                    pass
            raise
        finally:
            if ctx_token is not None:
                direct_execution_id.reset(ctx_token)
            if sess_token is not None:
                direct_session_id.reset(sess_token)

    async def _execute_runner_tool(
        self,
        runner_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute tool on a runner via WebSocket (DEC-123).

        Routes tool calls to runners based on prefix:
        - "runner-name:tool" -> Route to runner via WebSocket

        Args:
            runner_name: Name of the runner to route to
            tool_name: Tool name (without prefix)
            arguments: Tool arguments

        Returns:
            MCP response
        """
        if not self._runner_registry:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message="Runner routing not configured (no runner registry)",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=503,
            )

        # Find runner by name
        runner = self._runner_registry.get_by_name(runner_name)
        if not runner:
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Runner '{runner_name}' not found",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=404,
            )

        # Check if runner is connected
        if not is_runner_connected(runner.id):
            raise AELError(
                code="TOOL_UNAVAILABLE",
                category=ErrorCategory.TOOL,
                message=f"Runner '{runner_name}' is not connected",
                tool_name=f"{runner_name}:{tool_name}",
                http_status=503,
            )

        # Instrument runner tool calls for telemetry
        # tool_name is already mcp__actual_tool at this point (runner prefix stripped above)
        # source = runner.name so dashboards can filter/group by runner identity
        # Extract bridge context for distributed topology labels (DEC-142)
        _bctx = bridge_context.get()
        _bridge_id = _bctx.bridge_id if _bctx else None
        _session_id = _bctx.session_id if _bctx else None

        # Telemetry lifecycle for runner tool calls (DEC-152)
        execution_id: str | None = None
        ctx_token = None
        sess_token = None
        # Bind session_id ContextVar so AELLogger emissions in this request
        # scope carry ael_session_id (S-304/M-082) for the events panel.
        if _session_id:
            sess_token = direct_session_id.set(_session_id)
        if self._telemetry_collector:
            try:
                execution_id = await self._telemetry_collector.start_execution(
                    execution_type=ExecutionType.DIRECT,
                    tool_name=tool_name,
                    source="runner",
                    caller_id=runner.name,
                    session_id=_session_id,
                    runner_id=runner.name,  # DEC-145
                    bridge_session_id=_bridge_id,  # DEC-145
                )
                ctx_token = direct_execution_id.set(execution_id)
            except Exception:
                pass  # Telemetry is non-critical

        start_ms = int(time.time() * 1000)
        bridge, short_tool = _split_tool_name(tool_name)
        async with instrument_tool_call(
            tool_name,  # "obsidian-mcp__list_files" — NOT prefixed with runner
            source=runner.name,  # human-readable runner name as tool_source
            runner_id=runner.name,  # human-readable name, NOT runner.id (UUID)
            bridge_id=_bridge_id,
        ) as telemetry_result:
            try:
                # START event
                if self._logger:
                    self._logger._log(
                        LogLevel.INFO,
                        "direct",
                        f"Direct runner tool call: {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_called",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                        },
                    )

                # S-304 / G4 — wrap runner invocation with synthetic step + tool_call row
                async with synthetic_direct_step(
                    self._telemetry_collector,
                    execution_id=execution_id,
                    tool_name=tool_name,
                ) as _step_id:
                    async with record_tool_call(
                        self._telemetry_collector,
                        execution_id=execution_id,
                        step_id=_step_id,
                        tool_name=tool_name,
                        params=arguments,
                        source=ToolCallSource.DIRECT,
                        runner_id=runner.name,
                        bridge_id=_bridge_id,
                        session_id=_session_id,
                    ) as _call_handle:
                        try:
                            result = await send_tool_call_to_runner(
                                runner_id=runner.id,
                                tool_name=tool_name,
                                arguments=arguments,
                                timeout=60.0,
                            )
                        except Exception as _exc:
                            _call_handle.set_error(_exc)
                            raise
                        # Unwrap runner's transport envelope: the WS executor
                        # returns {"status": "success"|"error", "result": {...}}
                        # but downstream branches expect MCP/output shape at the
                        # top level (content/isError or output/error). Strip the
                        # envelope so set_result captures the real payload and
                        # response_bytes is non-zero. Map the inner ``error``
                        # field to MCP's ``isError`` so downstream (and the
                        # caller) sees a compliant shape.
                        if (
                            isinstance(result, dict)
                            and isinstance(result.get("status"), str)
                            and "result" in result
                            and isinstance(result["result"], dict)
                        ):
                            inner = result["result"]
                            inner_err = inner.get("error")
                            if inner_err:
                                # Surface as MCP-error so existing error branch
                                # (``elif "error" in result``) handles it.
                                result = {"error": str(inner_err)}
                            else:
                                # S-305: normalize inner ``content`` into the
                                # MCP-spec content-block list so strict TS-SDK
                                # clients (Claude Desktop, Cursor, Augment)
                                # don't choke on ``r.content.map`` when the
                                # runner returns a raw dict/string payload.
                                # Forward-compat: if a runner already emits a
                                # list of typed blocks, pass it through.
                                inner_content = inner.get("content")
                                if (
                                    isinstance(inner_content, list)
                                    and inner_content
                                    and all(
                                        isinstance(b, dict) and "type" in b for b in inner_content
                                    )
                                ):
                                    blocks = inner_content
                                else:
                                    text = (
                                        inner_content
                                        if isinstance(inner_content, str)
                                        else json.dumps(inner_content, default=str)
                                    )
                                    blocks = [{"type": "text", "text": text}]
                                result = {
                                    "content": blocks,
                                    "isError": False,
                                }
                        # Treat MCP-format isError or output-format error as failure
                        _is_err = bool(result.get("isError")) or bool(result.get("error"))
                        _runner_err_msg = ""
                        if _is_err:
                            # Extract the most informative error message
                            # available.  Prefer the top-level ``error`` key
                            # (output-format).  Fall back to the first text
                            # content block (MCP-format ``isError`` path).
                            _runner_err_msg = str(result.get("error") or "")
                            if not _runner_err_msg:
                                for _blk in result.get("content") or []:
                                    if isinstance(_blk, dict) and _blk.get("type") == "text":
                                        _runner_err_msg = str(_blk.get("text") or "")
                                        break
                            _call_handle.set_error(
                                TelemetryErrorRecord(
                                    code="TOOL_FAILED",
                                    category="tool",
                                    message=_runner_err_msg or "runner returned error",
                                )
                            )
                        else:
                            _call_handle.set_result(result.get("output") or result.get("content"))

                duration_ms = int(time.time() * 1000) - start_ms

                # Result from runner is in format: {"output": ..., "error": ...}
                # or {"content": [...], "isError": ...} if runner returns MCP format
                is_error = False
                if "content" in result:
                    # Runner returned MCP format directly
                    is_error = result.get("isError", False)
                    record_tool_result(telemetry_result, success=not is_error)
                    # Reuse _runner_err_msg extracted above (always
                    # defined; empty when there was no error).
                    _mcp_err_detail = _runner_err_msg
                    if self._logger:
                        if is_error:
                            self._logger._log(
                                LogLevel.ERROR,
                                "direct",
                                f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                                {
                                    "source": "tool",
                                    "event": "direct_tool_failed",
                                    "tool_name": short_tool,
                                    "bridge": bridge,
                                    "runner_id": runner.name,
                                    "duration_ms": duration_ms,
                                    "error": _mcp_err_detail or "runner returned error",
                                    "error_type": "TOOL_FAILED",
                                },
                            )
                        else:
                            self._logger._log(
                                LogLevel.INFO,
                                "direct",
                                f"Direct runner tool completed ({duration_ms}ms): {tool_name}",
                                {
                                    "source": "tool",
                                    "event": "direct_tool_completed",
                                    "tool_name": short_tool,
                                    "bridge": bridge,
                                    "runner_id": runner.name,
                                    "duration_ms": duration_ms,
                                },
                            )
                    # Chain detection for MCP-format runner results (DEC-057)
                    _rchain_log = logging.getLogger("ploston.chain_detection")
                    if self._chain_detector and not is_error:
                        try:
                            # Extract text content from MCP content blocks
                            chain_output = ""
                            for block in result.get("content", []):
                                if block.get("type") == "text":
                                    chain_output += block.get("text", "")
                            _rchain_log.debug(
                                "_execute_runner_tool (MCP-format): invoking chain detection "
                                "tool=%s runner=%s session=%s bridge=%s",
                                tool_name,
                                runner.name,
                                _session_id or _bridge_id or runner.name,
                                _bridge_id,
                            )
                            await self._chain_detector.process_tool_call(
                                tool_name=tool_name,
                                params=arguments,
                                result=chain_output,
                                runner_id=runner.name,
                                bridge_id=_bridge_id,
                                session_id=_session_id or _bridge_id or runner.name,
                            )
                        except Exception as _rce:
                            _rchain_log.warning(
                                "_execute_runner_tool: chain detection error (MCP-format) "
                                "tool=%s: %s",
                                tool_name,
                                _rce,
                            )
                    # End telemetry for MCP-format result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            if is_error:
                                await self._telemetry_collector.end_execution(
                                    execution_id=execution_id,
                                    status=TelemetryExecutionStatus.FAILED,
                                    error=TelemetryErrorRecord(
                                        code="TOOL_FAILED",
                                        category="tool",
                                        message=_mcp_err_detail or "runner returned error",
                                    ),
                                )
                            else:
                                await self._telemetry_collector.end_execution(
                                    execution_id=execution_id,
                                    status=TelemetryExecutionStatus.COMPLETED,
                                )
                            execution_id = None  # Prevent double-end in finally
                        except Exception:
                            pass
                    return result
                elif "error" in result and result.get("error"):
                    record_tool_result(telemetry_result, success=False, error_code="TOOL_FAILED")
                    if self._logger:
                        self._logger._log(
                            LogLevel.ERROR,
                            "direct",
                            f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                            {
                                "source": "tool",
                                "event": "direct_tool_failed",
                                "tool_name": short_tool,
                                "bridge": bridge,
                                "runner_id": runner.name,
                                "duration_ms": duration_ms,
                                "error": str(result["error"]),
                                "error_type": "TOOL_FAILED",
                            },
                        )
                    # End telemetry for error result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            await self._telemetry_collector.end_execution(
                                execution_id=execution_id,
                                status=TelemetryExecutionStatus.FAILED,
                                error=TelemetryErrorRecord(
                                    code="TOOL_FAILED",
                                    category="tool",
                                    message=str(result["error"]),
                                ),
                            )
                            execution_id = None
                        except Exception:
                            pass
                    return {
                        "content": [{"type": "text", "text": str(result["error"])}],
                        "isError": True,
                    }
                else:
                    output = result.get("output", result)
                    record_tool_result(telemetry_result, success=True)
                    if self._logger:
                        self._logger._log(
                            LogLevel.INFO,
                            "direct",
                            f"Direct runner tool completed ({duration_ms}ms): {tool_name}",
                            {
                                "source": "tool",
                                "event": "direct_tool_completed",
                                "tool_name": short_tool,
                                "bridge": bridge,
                                "runner_id": runner.name,
                                "duration_ms": duration_ms,
                            },
                        )
                    # Chain detection for output-format runner results (DEC-057)
                    if self._chain_detector:
                        try:
                            _rchain_log2 = logging.getLogger("ploston.chain_detection")
                            _rchain_log2.debug(
                                "_execute_runner_tool (output-format): invoking chain "
                                "detection tool=%s runner=%s session=%s bridge=%s",
                                tool_name,
                                runner.name,
                                _session_id or _bridge_id or runner.name,
                                _bridge_id,
                            )
                            await self._chain_detector.process_tool_call(
                                tool_name=tool_name,
                                params=arguments,
                                result=output,
                                runner_id=runner.name,
                                bridge_id=_bridge_id,
                                session_id=_session_id or _bridge_id or runner.name,
                            )
                        except Exception as _rce2:
                            logging.getLogger("ploston.chain_detection").warning(
                                "_execute_runner_tool: chain detection error (output-format) "
                                "tool=%s: %s",
                                tool_name,
                                _rce2,
                            )
                    # End telemetry for successful output result (DEC-152)
                    if self._telemetry_collector and execution_id:
                        try:
                            await self._telemetry_collector.end_execution(
                                execution_id=execution_id,
                                status=TelemetryExecutionStatus.COMPLETED,
                                outputs={"output": str(output)[:500]} if output else None,
                            )
                            execution_id = None
                        except Exception:
                            pass
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    json.dumps(output) if not isinstance(output, str) else output
                                ),
                            }
                        ],
                        "isError": False,
                    }

            except TimeoutError:
                duration_ms = int(time.time() * 1000) - start_ms
                if self._logger:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                            "duration_ms": duration_ms,
                            "error": f"Tool call to runner '{runner_name}' timed out",
                            "error_type": "TimeoutError",
                        },
                    )
                raise AELError(
                    code="TOOL_TIMEOUT",
                    category=ErrorCategory.TOOL,
                    message=f"Tool call to runner '{runner_name}' timed out",
                    tool_name=f"{runner_name}:{tool_name}",
                    http_status=504,
                )
            except AELError:
                raise
            except Exception as e:
                duration_ms = int(time.time() * 1000) - start_ms
                if self._logger:
                    self._logger._log(
                        LogLevel.ERROR,
                        "direct",
                        f"Direct runner tool failed ({duration_ms}ms): {tool_name}",
                        {
                            "source": "tool",
                            "event": "direct_tool_failed",
                            "tool_name": short_tool,
                            "bridge": bridge,
                            "runner_id": runner.name,
                            "duration_ms": duration_ms,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                logger.exception(f"Error routing tool to runner: {e}")
                raise create_error(
                    "TOOL_EXECUTION_FAILED",
                    message=f"Tool call to runner '{runner_name}' failed: {e}",
                    tool_name=f"{runner_name}:{tool_name}",
                ) from e
            finally:
                # End any dangling telemetry execution (DEC-152)
                if self._telemetry_collector and execution_id:
                    try:
                        await self._telemetry_collector.end_execution(
                            execution_id=execution_id,
                            status=TelemetryExecutionStatus.FAILED,
                            error=TelemetryErrorRecord(
                                code="TOOL_EXECUTION_FAILED",
                                category="tool",
                                message="Execution ended without explicit completion",
                            ),
                        )
                    except Exception:
                        pass
                if ctx_token is not None:
                    direct_execution_id.reset(ctx_token)
                if sess_token is not None:
                    direct_session_id.reset(sess_token)

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
