"""Unit tests for WorkflowRouter.

Tests for S-181: CP Workflow Router
- UT-069: test_router_init
- UT-070: test_router_tool_prefix_parse
- UT-071: test_router_no_prefix_is_cp
- UT-072: test_routing_all_cp
- UT-073: test_routing_any_runner
- UT-074: test_routing_mixed_tools
- UT-075: test_dispatch_to_runner
- UT-076: test_tool_call_to_runner
"""

from dataclasses import dataclass, field

import pytest

from ploston_core.runner_management.registry import RunnerRegistry
from ploston_core.runner_management.router import (
    RoutingTarget,
    RunnerUnavailableError,
    ToolUnavailableError,
    WorkflowRouter,
    extract_tools_from_workflow,
    parse_tool_prefix,
)


# Mock WorkflowDefinition for testing
@dataclass
class MockStep:
    """Mock workflow step."""

    name: str
    tool: str | None = None
    code: str | None = None


@dataclass
class MockWorkflowDefinition:
    """Mock workflow definition."""

    name: str
    steps: list[MockStep] = field(default_factory=list)


class TestRouterInit:
    """UT-069: Router component initializes."""

    def test_router_init(self):
        """Test router initialization."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        assert router._registry is registry

    def test_router_with_empty_registry(self):
        """Test router works with empty registry."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="slack_post")],
        )

        # Should route to CP since no runners
        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.CP


class TestToolPrefixParse:
    """UT-070: Parse runner__mcp__tool format."""

    def test_parse_prefixed_tool(self):
        """Test parsing tool with runner and MCP prefix."""
        runner, mcp, tool = parse_tool_prefix("mac__fs__read_file")
        assert runner == "mac"
        assert mcp == "fs"
        assert tool == "read_file"

    def test_parse_unprefixed_tool(self):
        """Test parsing tool without prefix."""
        runner, mcp, tool = parse_tool_prefix("slack_post")
        assert runner is None
        assert mcp is None
        assert tool == "slack_post"

    def test_parse_tool_with_double_underscore_in_name(self):
        """Test parsing tool with __ in tool name."""
        runner, mcp, tool = parse_tool_prefix("mac__fs__read__special__file")
        assert runner == "mac"
        assert mcp == "fs"
        assert tool == "read__special__file"

    def test_parse_incomplete_prefix(self):
        """Test parsing tool with only one prefix part."""
        runner, mcp, tool = parse_tool_prefix("mac__read_file")
        assert runner is None
        assert mcp is None
        assert tool == "mac__read_file"


class TestNoPrefixIsCP:
    """UT-071: No prefix = CP tool."""

    def test_unprefixed_tool_routes_to_cp(self):
        """Test that unprefixed tools route to CP."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="slack_post")],
        )

        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.CP
        assert decision.cp_tools == ["slack_post"]


class TestRoutingAllCP:
    """UT-072: All CP tools → run on CP."""

    def test_all_cp_tools(self):
        """Test workflow with all CP tools."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="slack_post"),
                MockStep(name="step2", tool="github_create_issue"),
                MockStep(name="step3", tool="jira_update"),
            ],
        )

        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.CP
        assert decision.cp_tools == ["slack_post", "github_create_issue", "jira_update"]
        assert decision.runner_name is None

    def test_code_only_workflow(self):
        """Test workflow with only code steps."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", code="print('hello')"),
                MockStep(name="step2", code="x = 1 + 1"),
            ],
        )

        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.CP
        assert "No tools required" in decision.reason


class TestRoutingAnyRunner:
    """UT-073: Any runner tool → run on runner."""

    def test_runner_tool_routes_to_runner(self):
        """Test workflow with runner tool routes to runner."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        # Runner stores tools as mcp__tool format
        registry.update_available_tools(runner.id, ["fs__read_file", "fs__write_file"])

        router = WorkflowRouter(registry)

        # Workflow uses runner__mcp__tool format
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="mac__fs__read_file")],
        )

        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.RUNNER
        assert decision.runner_name == "mac"
        assert decision.runner_tools == ["fs__read_file"]

    def test_runner_not_connected_fails(self):
        """Test that disconnected runner fails routing."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")  # Created but not connected

        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="mac__fs__read_file")],
        )

        with pytest.raises(RunnerUnavailableError, match="not connected"):
            router.analyze(workflow)

    def test_runner_missing_tool_fails(self):
        """Test that missing tool fails routing."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs__read_file"])  # No fs__write_file

        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="mac__fs__write_file")],
        )

        with pytest.raises(ToolUnavailableError, match="does not have tools"):
            router.analyze(workflow)


class TestRoutingMixedTools:
    """UT-074: Mixed → run on runner + proxy."""

    def test_mixed_tools_routes_to_runner(self):
        """Test workflow with mixed tools routes to runner."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs__read_file"])

        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="mac__fs__read_file"),
                MockStep(name="step2", tool="slack_post"),  # CP tool
            ],
        )

        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.RUNNER
        assert decision.runner_name == "mac"
        assert decision.runner_tools == ["fs__read_file"]
        assert decision.cp_tools == ["slack_post"]

    def test_multiple_runners_fails(self):
        """Test that multiple runners in one workflow fails."""
        registry = RunnerRegistry()
        runner1, _ = registry.create("laptop1")
        runner2, _ = registry.create("laptop2")
        registry.set_connected(runner1.id)
        registry.set_connected(runner2.id)
        registry.update_available_tools(runner1.id, ["fs__read_file"])
        registry.update_available_tools(runner2.id, ["docker__run"])

        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="laptop1__fs__read_file"),
                MockStep(name="step2", tool="laptop2__docker__run"),
            ],
        )

        with pytest.raises(ToolUnavailableError, match="multiple runners"):
            router.analyze(workflow)


class TestDispatchToRunner:
    """UT-075: Dispatch workflow/execute."""

    def test_get_runner_for_workflow(self):
        """Test getting runner for a workflow."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs__read_file"])

        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="mac__fs__read_file")],
        )

        found = router.get_runner_for_workflow(workflow)
        assert found is not None
        assert found.name == "mac"

    def test_get_runner_for_cp_workflow(self):
        """Test getting runner for CP workflow returns None."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="slack_post")],
        )

        found = router.get_runner_for_workflow(workflow)
        assert found is None

    def test_should_run_on_runner(self):
        """Test checking if workflow should run on runner."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs__read_file"])

        router = WorkflowRouter(registry)

        runner_workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="mac__fs__read_file")],
        )
        cp_workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="slack_post")],
        )

        assert router.should_run_on_runner(runner_workflow) is True
        assert router.should_run_on_runner(cp_workflow) is False


class TestToolCallToRunner:
    """UT-076: Send tool/call message."""

    def test_get_tool_runner(self):
        """Test getting runner for a specific tool."""
        registry = RunnerRegistry()
        runner, _ = registry.create("mac")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs__read_file"])

        router = WorkflowRouter(registry)

        found = router.get_tool_runner("mac__fs__read_file")
        assert found is not None
        assert found.name == "mac"

    def test_get_tool_runner_cp_tool(self):
        """Test getting runner for CP tool returns None."""
        registry = RunnerRegistry()
        router = WorkflowRouter(registry)

        found = router.get_tool_runner("slack_post")
        assert found is None


class TestExtractTools:
    """Test extract_tools_from_workflow helper."""

    def test_extract_tools(self):
        """Test extracting tools from workflow."""
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="fs_read"),
                MockStep(name="step2", code="print('hello')"),
                MockStep(name="step3", tool="slack_post"),
            ],
        )

        tools = extract_tools_from_workflow(workflow)
        assert tools == ["fs_read", "slack_post"]

    def test_extract_tools_empty(self):
        """Test extracting tools from empty workflow."""
        workflow = MockWorkflowDefinition(name="test", steps=[])
        tools = extract_tools_from_workflow(workflow)
        assert tools == []
