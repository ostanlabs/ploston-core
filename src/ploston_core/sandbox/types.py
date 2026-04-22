"""Sandbox types and interfaces."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ploston_core.engine.normalize import normalize_mcp_response
from ploston_core.errors import create_error
from ploston_core.types import LogLevel, StepOutput

if TYPE_CHECKING:
    from ploston_core.logging import AELLogger


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""

    timeout: int = 30
    max_tool_calls: int = 10
    allowed_imports: list[str] | None = None  # None = use defaults

    def __post_init__(self) -> None:
        """Initialize default allowed imports if not provided."""
        if self.allowed_imports is None:
            # Sync-required with PythonExecConfig.default_imports (config/models.py —
            # PRODUCTION GATE) and SAFE_IMPORTS (sandbox.py).
            self.allowed_imports = [
                # standard library
                "json",
                "math",
                "datetime",
                "_strptime",  # S-272 T-865: required by datetime.strptime() (lazy import)
                "time",
                "random",
                "itertools",
                "functools",
                "collections",
                "typing",
                "re",
                "decimal",
                "statistics",
                "operator",
                "copy",
                "uuid",
                "hashlib",
                "io",  # T-688 audit: needed for io.BytesIO in PDF parsing
                # third-party additions (S-225)
                "anthropic",
                "pypdf",
            ]


@dataclass
class RunnerContext:
    """Raw runner source values for call_mcp() resolution.

    call_mcp() applies the priority chain: defaults_runner > runner_name.
    This is a transparent data carrier, not a pre-resolved cache.
    """

    runner_name: str | None = None  # raw value from X-Ploston-Runner header
    defaults_runner: str | None = None  # raw value from workflow.defaults.runner
    step_id: str | None = None  # for log correlation
    execution_id: str | None = None  # for log correlation


@dataclass
class WorkflowMeta:
    """Workflow metadata available in sandbox code as context.workflow."""

    name: str
    version: str
    execution_id: str
    start_time: str  # ISO 8601 string


@dataclass
class SandboxContext:
    """Context available to code execution.

    Access patterns in code:
    - context.inputs["url"] or context.inputs.get("url")
    - context.steps["fetch"].output
    - context.config["timeout"]
    - context.tools.call("tool_name", {...})
    - context.tools.call_mcp("github", "list_commits", {...})
    - context.log("debug message")
    """

    inputs: dict[str, Any]
    steps: dict[str, StepOutput]  # Uses shared StepOutput type
    config: dict[str, Any]

    # Tool call capability
    tools: "ToolCallInterface"

    # Runner context for call_mcp() resolution (optional, None for legacy paths)
    runner_context: "RunnerContext | None" = None

    # Workflow metadata (name, version, execution_id, start_time)
    workflow: "WorkflowMeta | None" = None

    # Internal debug log buffer (populated via context.log())
    _debug_log: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        """Append a debug message to the step's debug log.

        Messages are captured and attached to the StepResult/StepOutput.
        Available in templates as {{ steps.<id>.debug_log }}.

        Args:
            message: Debug message to log
        """
        self._debug_log.append(str(message))


@dataclass
class CodeExecutionResult:
    """Result of code execution in sandbox.

    Note: Named CodeExecutionResult to distinguish from
    WorkflowEngine's ExecutionResult (workflow-level).
    """

    success: bool
    output: Any
    error: Any = None  # AELError
    duration_ms: int = 0
    tool_calls: int = 0


# ─────────────────────────────────────────────────────────────────
# Tool Calling Protocol (breaks circular dependency)
# ─────────────────────────────────────────────────────────────────


@runtime_checkable
class ToolCallerProtocol(Protocol):
    """Protocol for tool calling capability.

    This breaks the circular dependency between ToolInvoker and Sandbox.
    ToolInvoker implements this protocol but Sandbox doesn't depend on it.
    """

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Call a tool and return its output."""
        ...


class ToolCallInterface:
    """Interface for calling tools from within sandbox.

    Wraps a ToolCallerProtocol with rate limiting, recursion prevention,
    and call_mcp() support for MCP tool resolution.
    """

    def __init__(
        self,
        tool_caller: ToolCallerProtocol,
        max_calls: int,
        logger: "AELLogger | None" = None,
        blocked_tools: list[str] | None = None,
        tool_registry: Any | None = None,
        runner_registry: Any | None = None,
        runner_context: "RunnerContext | None" = None,
    ):
        """Initialize tool call interface.

        Args:
            tool_caller: Tool caller implementation
            max_calls: Maximum number of tool calls allowed
            logger: Optional logger
            blocked_tools: List of tools that cannot be called (default: ["python_exec"])
            tool_registry: Optional ToolRegistry for CP-direct resolution in call_mcp
            runner_registry: Optional RunnerRegistry for runner inference in call_mcp
            runner_context: Optional RunnerContext for runner resolution in call_mcp
        """
        self._caller = tool_caller
        self._max_calls = max_calls
        self._call_count = 0
        self._logger = logger
        self._blocked_tools = blocked_tools or ["python_exec"]  # Prevent recursion
        self._tool_registry = tool_registry
        self._runner_registry = runner_registry
        self._runner_context = runner_context
        self._step_id = runner_context.step_id if runner_context else None
        self._execution_id = runner_context.execution_id if runner_context else None

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Call a tool from within code block.

        Args:
            tool_name: Name of the tool to call
            params: Tool parameters

        Returns:
            Tool output

        Raises:
            AELError(RESOURCE_EXHAUSTED) if max calls exceeded
            AELError(TOOL_REJECTED) if tool is blocked
            AELError(TOOL_*) for tool errors
        """
        from ploston_core.runner_management.router import normalize_tool_name_for_metrics

        # Check recursion prevention
        if tool_name in self._blocked_tools:
            raise create_error(
                "TOOL_REJECTED",
                tool_name=tool_name,
                reason=f"Tool '{tool_name}' cannot be called from within code blocks",
            )

        # Check rate limit
        if self._call_count >= self._max_calls:
            if self._logger:
                self._logger._log(
                    LogLevel.WARN,
                    "sandbox",
                    "Tool call rate limit reached",
                    {
                        "call_count": self._call_count,
                        "max_calls": self._max_calls,
                        "step_id": self._step_id,
                        "execution_id": self._execution_id,
                        "event": "sandbox_rate_limit_exhausted",
                    },
                )
            raise create_error(
                "RESOURCE_EXHAUSTED",
                resource=f"tool calls (max {self._max_calls})",
            )

        self._call_count += 1

        metric_name, runner_id = normalize_tool_name_for_metrics(tool_name)

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "sandbox",
                f"Tool call: {metric_name}",
                {
                    "tool_name": metric_name,
                    "runner": runner_id,
                    "source": "sandbox",
                    "call_count": self._call_count,
                    "step_id": self._step_id,
                    "execution_id": self._execution_id,
                },
            )

        result = await self._caller.call(tool_name, params)

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "sandbox",
                f"Tool call complete: {metric_name}",
                {
                    "tool_name": metric_name,
                    "runner": runner_id,
                },
            )

        return normalize_mcp_response(result)

    async def call_mcp(
        self,
        mcp: str,
        tool: str,
        params: dict[str, Any],
        runner: str | None = None,
    ) -> Any:
        """Call a tool by MCP server name and tool name.

        Resolution priority:
        1. CP-direct (ToolRegistry) — tool registered directly on Control Plane
        2. defaults_runner from workflow YAML
        3. runner_name from bridge context (X-Ploston-Runner header)
        4. Single-match inference from RunnerRegistry

        Blocked-tool check is on the bare tool name regardless of mcp server.
        If a tool name is blocked (e.g. python_exec), it is blocked on all
        MCP servers as defense in depth.

        Args:
            mcp: MCP server name
            tool: Tool name on the MCP server
            params: Tool parameters
            runner: Optional explicit runner name (reserved for F-072)

        Returns:
            Tool output

        Raises:
            AELError(TOOL_REJECTED) if tool is blocked
            AELError(RESOURCE_EXHAUSTED) if max calls exceeded
            AELError(TOOL_UNAVAILABLE) if tool cannot be resolved
        """
        # Recursion prevention — bare tool name check
        if tool in self._blocked_tools:
            raise create_error(
                "TOOL_REJECTED",
                tool_name=f"{mcp}__{tool}",
                reason=f"Tool '{tool}' cannot be called from within code blocks",
            )

        # Rate limit
        if self._call_count >= self._max_calls:
            if self._logger:
                self._logger._log(
                    LogLevel.WARN,
                    "sandbox",
                    "Tool call rate limit reached",
                    {
                        "call_count": self._call_count,
                        "max_calls": self._max_calls,
                        "step_id": self._step_id,
                        "execution_id": self._execution_id,
                        "event": "sandbox_rate_limit_exhausted",
                    },
                )
            raise create_error(
                "RESOURCE_EXHAUSTED",
                resource=f"tool calls (max {self._max_calls})",
            )

        # Step 1: CP-direct lookup by server_name
        if self._tool_registry:
            cp_tools = self._tool_registry.list_tools(server_name=mcp)
            for tool_def in cp_tools:
                if tool_def.name == tool:
                    self._call_count += 1
                    if self._logger:
                        self._logger._log(
                            LogLevel.INFO,
                            "sandbox",
                            f"call_mcp CP-direct: {mcp}/{tool}",
                            {
                                "tool_name": f"{mcp}__{tool}",
                                "runner": None,
                                "source": "cp",
                                "step_id": self._step_id,
                                "execution_id": self._execution_id,
                            },
                        )
                    return normalize_mcp_response(await self._caller.call(tool, params))

        # Step 2: Resolve runner
        # Priority: explicit arg (F-072) > defaults_runner > runner_name > inference
        effective_runner = runner

        if not effective_runner and self._runner_context:
            effective_runner = (
                self._runner_context.defaults_runner or self._runner_context.runner_name
            )

        # Single-match inference using RunnerRegistry._get_tool_name()
        if not effective_runner and self._runner_registry:
            matches = [
                r
                for r in self._runner_registry.list()
                if r.status.value == "connected"
                and any(
                    self._runner_registry._get_tool_name(e).startswith(f"{mcp}__")
                    for e in (r.available_tools or [])
                )
            ]
            if len(matches) == 1:
                effective_runner = matches[0].name
            elif len(matches) > 1:
                names = sorted(r.name for r in matches)
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    tool_name=f"{mcp}__{tool}",
                    reason=(
                        f"MCP server '{mcp}' found on multiple runners: {names}. "
                        "Add defaults.runner to the workflow to disambiguate."
                    ),
                )

        # Step 3: Build canonical name and dispatch
        if effective_runner:
            canonical = f"{effective_runner}__{mcp}__{tool}"
            self._call_count += 1
            if self._logger:
                self._logger._log(
                    LogLevel.INFO,
                    "sandbox",
                    f"call_mcp runner: {mcp}/{tool}",
                    {
                        "tool_name": f"{mcp}__{tool}",
                        "runner": effective_runner,
                        "source": "runner",
                        "step_id": self._step_id,
                        "execution_id": self._execution_id,
                    },
                )
            return normalize_mcp_response(await self._caller.call(canonical, params))

        raise create_error(
            "TOOL_UNAVAILABLE",
            tool_name=f"{mcp}__{tool}",
            reason=(
                f"Tool '{tool}' not found on MCP server '{mcp}'. "
                "No matching CP-direct tool and no runner context available. "
                "Use workflow_schema to see available tools."
            ),
        )


# ─────────────────────────────────────────────────────────────────
# Security Constants
# ─────────────────────────────────────────────────────────────────

DISALLOWED_BUILTINS = [
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "globals",
    "locals",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
]

# COMMON_IMPORTS and STANDARD_IMPORTS were removed in S-225 —
# superseded by SandboxConfig.allowed_imports defaults and SAFE_IMPORTS in sandbox.py.
