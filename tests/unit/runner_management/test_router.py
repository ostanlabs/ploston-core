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

import pytest
from dataclasses import dataclass, field

from ploston_core.runner_management.registry import RunnerRegistry, RunnerStatus
from ploston_core.runner_management.router import (
    RoutingDecision,
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
    """UT-070: Parse runner:tool format."""
    
    def test_parse_prefixed_tool(self):
        """Test parsing tool with runner prefix."""
        runner, tool = parse_tool_prefix("marc-laptop:fs_read")
        assert runner == "marc-laptop"
        assert tool == "fs_read"
    
    def test_parse_unprefixed_tool(self):
        """Test parsing tool without prefix."""
        runner, tool = parse_tool_prefix("slack_post")
        assert runner is None
        assert tool == "slack_post"
    
    def test_parse_tool_with_colon_in_name(self):
        """Test parsing tool with colon in tool name."""
        runner, tool = parse_tool_prefix("runner:tool:with:colons")
        assert runner == "runner"
        assert tool == "tool:with:colons"


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
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read", "fs_write"])
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="marc-laptop:fs_read")],
        )
        
        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.RUNNER
        assert decision.runner_name == "marc-laptop"
        assert decision.runner_tools == ["fs_read"]
    
    def test_runner_not_connected_fails(self):
        """Test that disconnected runner fails routing."""
        registry = RunnerRegistry()
        registry.create("marc-laptop")  # Not connected
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="marc-laptop:fs_read")],
        )
        
        with pytest.raises(RunnerUnavailableError, match="not connected"):
            router.analyze(workflow)
    
    def test_runner_missing_tool_fails(self):
        """Test that missing tool fails routing."""
        registry = RunnerRegistry()
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])  # No fs_write
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="marc-laptop:fs_write")],
        )
        
        with pytest.raises(ToolUnavailableError, match="does not have tools"):
            router.analyze(workflow)


class TestRoutingMixedTools:
    """UT-074: Mixed → run on runner + proxy."""
    
    def test_mixed_tools_routes_to_runner(self):
        """Test workflow with mixed tools routes to runner."""
        registry = RunnerRegistry()
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="marc-laptop:fs_read"),
                MockStep(name="step2", tool="slack_post"),  # CP tool
            ],
        )
        
        decision = router.analyze(workflow)
        assert decision.target == RoutingTarget.RUNNER
        assert decision.runner_name == "marc-laptop"
        assert decision.runner_tools == ["fs_read"]
        assert decision.cp_tools == ["slack_post"]
    
    def test_multiple_runners_fails(self):
        """Test that multiple runners in one workflow fails."""
        registry = RunnerRegistry()
        runner1, _ = registry.create("laptop-1")
        runner2, _ = registry.create("laptop-2")
        registry.set_connected(runner1.id)
        registry.set_connected(runner2.id)
        registry.update_available_tools(runner1.id, ["fs_read"])
        registry.update_available_tools(runner2.id, ["docker_run"])
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[
                MockStep(name="step1", tool="laptop-1:fs_read"),
                MockStep(name="step2", tool="laptop-2:docker_run"),
            ],
        )
        
        with pytest.raises(ToolUnavailableError, match="multiple runners"):
            router.analyze(workflow)


class TestDispatchToRunner:
    """UT-075: Dispatch workflow/execute."""
    
    def test_get_runner_for_workflow(self):
        """Test getting runner for a workflow."""
        registry = RunnerRegistry()
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])
        
        router = WorkflowRouter(registry)
        
        workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="marc-laptop:fs_read")],
        )
        
        found = router.get_runner_for_workflow(workflow)
        assert found is not None
        assert found.name == "marc-laptop"
    
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
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])
        
        router = WorkflowRouter(registry)
        
        runner_workflow = MockWorkflowDefinition(
            name="test",
            steps=[MockStep(name="step1", tool="marc-laptop:fs_read")],
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
        runner, _ = registry.create("marc-laptop")
        registry.set_connected(runner.id)
        registry.update_available_tools(runner.id, ["fs_read"])
        
        router = WorkflowRouter(registry)
        
        found = router.get_tool_runner("marc-laptop:fs_read")
        assert found is not None
        assert found.name == "marc-laptop"
    
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
