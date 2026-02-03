"""Sandbox types and interfaces."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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
            # Keep in sync with SAFE_IMPORTS in sandbox.py
            self.allowed_imports = [
                "json",
                "math",
                "datetime",
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
                "hashlib",  # Additional for hashing
            ]


@dataclass
class SandboxContext:
    """Context available to code execution.

    Access patterns in code:
    - context.inputs["url"] or context.inputs.get("url")
    - context.steps["fetch"].output
    - context.config["timeout"]
    - context.tools.call("tool_name", {...})
    """

    inputs: dict[str, Any]
    steps: dict[str, StepOutput]  # Uses shared StepOutput type
    config: dict[str, Any]

    # Tool call capability
    tools: "ToolCallInterface"


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

    Wraps a ToolCallerProtocol with rate limiting and recursion prevention.
    """

    def __init__(
        self,
        tool_caller: ToolCallerProtocol,
        max_calls: int,
        logger: "AELLogger | None" = None,
        blocked_tools: list[str] | None = None,
    ):
        """Initialize tool call interface.

        Args:
            tool_caller: Tool caller implementation
            max_calls: Maximum number of tool calls allowed
            logger: Optional logger
            blocked_tools: List of tools that cannot be called (default: ["python_exec"])
        """
        self._caller = tool_caller
        self._max_calls = max_calls
        self._call_count = 0
        self._logger = logger
        self._blocked_tools = blocked_tools or ["python_exec"]  # Prevent recursion

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
        # Check recursion prevention
        if tool_name in self._blocked_tools:
            raise create_error(
                "TOOL_REJECTED",
                tool_name=tool_name,
                reason=f"Tool '{tool_name}' cannot be called from within code blocks",
            )

        # Check rate limit
        if self._call_count >= self._max_calls:
            raise create_error(
                "RESOURCE_EXHAUSTED",
                message=f"Max tool calls ({self._max_calls}) exceeded",
            )

        self._call_count += 1

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "sandbox",
                f"Tool call: {tool_name}",
                {"tool_name": tool_name, "call_count": self._call_count},
            )

        result = await self._caller.call(tool_name, params)

        if self._logger:
            self._logger._log(
                LogLevel.INFO,
                "sandbox",
                f"Tool call complete: {tool_name}",
                {"tool_name": tool_name},
            )

        return result


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

STANDARD_IMPORTS = [
    "json",
    "re",
    "datetime",
    "math",
    "random",
    "typing",
    "collections",
    "itertools",
    "functools",
    "hashlib",
    "uuid",
    "base64",
    "urllib.parse",
]

COMMON_IMPORTS = STANDARD_IMPORTS + [
    "requests",
    "pydantic",
    "jmespath",
    "dateutil",
    "yaml",
]
