"""Tests for T-689: on_missing_tool: skip workflow schema field."""

from ploston_core.types import OnMissingTool, StepType
from ploston_core.workflow.parser import parse_workflow_yaml
from ploston_core.workflow.schema_generator import generate_workflow_schema


class TestOnMissingToolParsing:
    """Test YAML parsing of on_missing_tool field."""

    def test_on_missing_tool_parsed_from_yaml(self) -> None:
        """on_missing_tool: skip should be parsed into StepDefinition."""
        yaml_content = """
name: test-workflow
steps:
  - id: fetch_slack
    tool: slack__get_channel_history
    params:
      channel: general
    on_missing_tool: skip
"""
        workflow = parse_workflow_yaml(yaml_content)
        assert workflow.steps[0].on_missing_tool == OnMissingTool.SKIP

    def test_on_missing_tool_fail_parsed(self) -> None:
        """on_missing_tool: fail should be parsed."""
        yaml_content = """
name: test-workflow
steps:
  - id: fetch_data
    tool: some_tool
    params: {}
    on_missing_tool: fail
"""
        workflow = parse_workflow_yaml(yaml_content)
        assert workflow.steps[0].on_missing_tool == OnMissingTool.FAIL

    def test_on_missing_tool_defaults_to_none(self) -> None:
        """Steps without on_missing_tool should default to None."""
        yaml_content = """
name: test-workflow
steps:
  - id: fetch_data
    tool: some_tool
    params: {}
"""
        workflow = parse_workflow_yaml(yaml_content)
        assert workflow.steps[0].on_missing_tool is None


class TestOnMissingToolEnum:
    """Test OnMissingTool enum values."""

    def test_skip_value(self) -> None:
        assert OnMissingTool.SKIP == "skip"

    def test_fail_value(self) -> None:
        assert OnMissingTool.FAIL == "fail"

    def test_from_string(self) -> None:
        assert OnMissingTool("skip") == OnMissingTool.SKIP
        assert OnMissingTool("fail") == OnMissingTool.FAIL


class TestSchemaGeneratorIncludesOnMissingTool:
    """Test that schema generator picks up the new field."""

    def test_schema_includes_on_missing_tool(self) -> None:
        """generate_workflow_schema() should include on_missing_tool in step properties."""
        schema = generate_workflow_schema()
        # The schema is a dict with sections; find the steps section
        # Schema generator returns a dict with 'properties' at top level
        assert "on_missing_tool" in str(schema), (
            "on_missing_tool should appear in the generated schema"
        )


class TestStepDefinitionField:
    """Test StepDefinition dataclass field."""

    def test_step_definition_on_missing_tool_default(self) -> None:
        from ploston_core.workflow.types import StepDefinition

        step = StepDefinition(id="test", tool="some_tool")
        assert step.on_missing_tool is None

    def test_step_definition_on_missing_tool_skip(self) -> None:
        from ploston_core.workflow.types import StepDefinition

        step = StepDefinition(id="test", tool="some_tool", on_missing_tool=OnMissingTool.SKIP)
        assert step.on_missing_tool == OnMissingTool.SKIP
        assert step.step_type == StepType.TOOL
