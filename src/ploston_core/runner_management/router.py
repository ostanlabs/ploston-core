"""Workflow Router for Control Plane.

Implements S-181: CP Workflow Router
- T-514: Workflow Router component
- T-515: Tool prefix analysis
- T-516: Routing decision logic
- T-517: Dispatch workflow to runner
- T-518: Send tool/call to runner
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ploston_core.runner_management.registry import Runner, RunnerRegistry
    from ploston_core.workflow.types import WorkflowDefinition


class RoutingTarget(str, Enum):
    """Where to execute a workflow."""

    CP = "cp"  # Control Plane
    RUNNER = "runner"  # Local Runner


@dataclass
class RoutingDecision:
    """Result of routing analysis.

    Attributes:
        target: Where to execute (CP or Runner)
        runner_name: Runner name if target is RUNNER
        runner_tools: Tools that will be executed on runner
        cp_tools: Tools that will be proxied to CP
        reason: Human-readable explanation
    """

    target: RoutingTarget
    runner_name: str | None = None
    runner_tools: list[str] | None = None
    cp_tools: list[str] | None = None
    reason: str = ""


class ToolUnavailableError(Exception):
    """Raised when a required tool is not available."""

    def __init__(self, tool_name: str, message: str | None = None):
        self.tool_name = tool_name
        super().__init__(message or f"Tool '{tool_name}' is not available")


class RunnerUnavailableError(Exception):
    """Raised when a required runner is not connected."""

    def __init__(self, runner_name: str, message: str | None = None):
        self.runner_name = runner_name
        super().__init__(message or f"Runner '{runner_name}' is not connected")


# Delimiter for runner tool prefixes (MCP-spec compliant)
# Format: runner__mcp__toolname (e.g., mac__fs__read_file)
TOOL_PREFIX_DELIMITER = "__"


def parse_tool_prefix(tool_name: str) -> tuple[str | None, str | None, str]:
    """Parse runner and MCP prefix from tool name.

    Tool names from runners use the format: runner__mcp__toolname
    This format is MCP-spec compliant (only alphanumeric, underscore, hyphen).

    Args:
        tool_name: Tool name, optionally prefixed with "runner__mcp__"

    Returns:
        Tuple of (runner_name, mcp_name, tool_name) where runner_name and mcp_name
        are None for CP tools (no prefix)

    Examples:
        "mac__fs__read_file" -> ("mac", "fs", "read_file")
        "mac__obs__create_accomplishment" -> ("mac", "obs", "create_accomplishment")
        "slack_post" -> (None, None, "slack_post")
    """
    parts = tool_name.split(TOOL_PREFIX_DELIMITER)
    if len(parts) >= 3:
        # runner__mcp__tool (tool may contain __ itself)
        runner_name = parts[0]
        mcp_name = parts[1]
        actual_tool = TOOL_PREFIX_DELIMITER.join(parts[2:])
        return runner_name, mcp_name, actual_tool
    # No prefix or incomplete prefix = CP tool
    return None, None, tool_name


def format_tool_name(runner_name: str, mcp_name: str, tool_name: str) -> str:
    """Format a tool name with runner and MCP prefix.

    Args:
        runner_name: Name of the runner
        mcp_name: Name of the MCP server
        tool_name: Original tool name

    Returns:
        Prefixed tool name in format: runner__mcp__toolname

    Examples:
        format_tool_name("mac", "fs", "read_file") -> "mac__fs__read_file"
    """
    return f"{runner_name}{TOOL_PREFIX_DELIMITER}{mcp_name}{TOOL_PREFIX_DELIMITER}{tool_name}"


def extract_tools_from_workflow(workflow: WorkflowDefinition) -> list[str]:
    """Extract all tool names from a workflow definition.

    Args:
        workflow: Workflow definition

    Returns:
        List of tool names used in the workflow
    """
    tools = []
    for step in workflow.steps:
        if step.tool:
            tools.append(step.tool)
    return tools


class WorkflowRouter:
    """Routes workflows to CP or Runner based on tool requirements.

    Per DEC-115: Run on runner when local tools are involved.
    Per DEC-123: CP namespaces runner tools with prefix routing.
    """

    def __init__(self, registry: RunnerRegistry) -> None:
        """Initialize the router.

        Args:
            registry: Runner registry for looking up runners and tools
        """
        self._registry = registry

    def analyze(self, workflow: WorkflowDefinition) -> RoutingDecision:
        """Analyze a workflow and decide where to execute it.

        Args:
            workflow: Workflow to analyze

        Returns:
            RoutingDecision with target and tool breakdown

        Raises:
            ToolUnavailableError: If a required tool is not available
            RunnerUnavailableError: If a required runner is not connected
        """
        tools = extract_tools_from_workflow(workflow)

        if not tools:
            # No tools = code-only workflow, run on CP
            return RoutingDecision(
                target=RoutingTarget.CP,
                reason="No tools required, executing on CP",
            )

        # Categorize tools by runner prefix
        runner_tools: dict[str, list[str]] = {}  # runner_name -> [tools]
        cp_tools: list[str] = []

        for tool in tools:
            runner_name, mcp_name, actual_tool = parse_tool_prefix(tool)
            if runner_name:
                if runner_name not in runner_tools:
                    runner_tools[runner_name] = []
                # Store the mcp__tool part for runner execution
                runner_tools[runner_name].append(
                    f"{mcp_name}{TOOL_PREFIX_DELIMITER}{actual_tool}" if mcp_name else actual_tool
                )
            else:
                cp_tools.append(tool)

        # If no runner tools, execute on CP
        if not runner_tools:
            return RoutingDecision(
                target=RoutingTarget.CP,
                cp_tools=cp_tools,
                reason="All tools are CP-level, executing on CP",
            )

        # If multiple runners referenced, that's an error (for now)
        if len(runner_tools) > 1:
            runners = list(runner_tools.keys())
            raise ToolUnavailableError(
                tool_name=tools[0],
                message=f"Workflow references multiple runners: {runners}. "
                "Multi-runner workflows not yet supported.",
            )

        # Single runner - validate it's connected and has the tools
        runner_name = list(runner_tools.keys())[0]
        runner = self._registry.get_by_name(runner_name)

        if not runner:
            raise RunnerUnavailableError(
                runner_name=runner_name,
                message=f"Runner '{runner_name}' not found",
            )

        if runner.status.value != "connected":
            raise RunnerUnavailableError(
                runner_name=runner_name,
                message=f"Runner '{runner_name}' is not connected",
            )

        # Validate runner has all required tools
        required_tools = runner_tools[runner_name]
        missing_tools = [t for t in required_tools if t not in runner.available_tools]

        if missing_tools:
            raise ToolUnavailableError(
                tool_name=missing_tools[0],
                message=f"Runner '{runner_name}' does not have tools: {missing_tools}",
            )

        return RoutingDecision(
            target=RoutingTarget.RUNNER,
            runner_name=runner_name,
            runner_tools=required_tools,
            cp_tools=cp_tools if cp_tools else None,
            reason=f"Workflow uses runner tools, dispatching to '{runner_name}'",
        )

    def route(self, workflow: WorkflowDefinition) -> RoutingDecision:
        """Route a workflow to the appropriate target.

        This is an alias for analyze() for clearer API.
        """
        return self.analyze(workflow)

    def get_runner_for_workflow(self, workflow: WorkflowDefinition) -> Runner | None:
        """Get the runner that should execute a workflow.

        Args:
            workflow: Workflow to analyze

        Returns:
            Runner if workflow should run on a runner, None for CP
        """
        try:
            decision = self.analyze(workflow)
            if decision.target == RoutingTarget.RUNNER and decision.runner_name:
                return self._registry.get_by_name(decision.runner_name)
        except (ToolUnavailableError, RunnerUnavailableError):
            pass
        return None

    def should_run_on_runner(self, workflow: WorkflowDefinition) -> bool:
        """Check if a workflow should run on a runner.

        Args:
            workflow: Workflow to check

        Returns:
            True if workflow should run on a runner
        """
        try:
            decision = self.analyze(workflow)
            return decision.target == RoutingTarget.RUNNER
        except (ToolUnavailableError, RunnerUnavailableError):
            return False

    def get_tool_runner(self, tool_name: str) -> Runner | None:
        """Get the runner for a specific tool.

        Args:
            tool_name: Tool name (may include runner__mcp__ prefix)

        Returns:
            Runner that has the tool, or None for CP tools
        """
        runner_name, _mcp_name, _actual_tool = parse_tool_prefix(tool_name)

        if not runner_name:
            # No prefix = CP tool
            return None

        return self._registry.get_runner_for_tool(tool_name)
