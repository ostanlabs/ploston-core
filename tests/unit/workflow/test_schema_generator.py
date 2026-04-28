"""Unit tests for workflow schema generator.

Tests that the generated schema is complete, accurate, and stays in sync
with the actual dataclass definitions (single source of truth).
"""

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.types.enums import BackoffType, OnError
from ploston_core.workflow.parser import parse_workflow_yaml
from ploston_core.workflow.schema_generator import generate_workflow_schema
from ploston_core.workflow.types import (
    InputDefinition,
    OutputDefinition,
    PackagesConfig,
    StepDefinition,
    WorkflowDefaults,
    WorkflowDefinition,
)


class TestSchemaCompleteness:
    """Every field on every workflow dataclass must appear in the generated schema."""

    def test_workflow_definition_fields_present(self):
        """All WorkflowDefinition fields appear in schema."""
        schema = generate_workflow_schema()
        wf_props = schema["properties"]

        # These are the user-facing YAML keys (source_path/yaml_content are internal)
        internal_fields = {"source_path", "yaml_content"}
        for f in dataclasses.fields(WorkflowDefinition):
            if f.name in internal_fields:
                continue
            assert f.name in wf_props, f"WorkflowDefinition.{f.name} missing from schema"

    def test_step_definition_fields_present(self):
        """All StepDefinition fields appear in the steps item schema."""
        schema = generate_workflow_schema()
        step_props = schema["properties"]["steps"]["items"]["properties"]

        for f in dataclasses.fields(StepDefinition):
            assert f.name in step_props, f"StepDefinition.{f.name} missing from schema"

    def test_input_definition_fields_present(self):
        """All InputDefinition fields appear in the inputs full-form schema."""
        schema = generate_workflow_schema()
        # inputs has multiple accepted forms; the full form should have all fields
        input_full_form = schema["properties"]["inputs"]["full_form_properties"]

        for f in dataclasses.fields(InputDefinition):
            assert f.name in input_full_form, f"InputDefinition.{f.name} missing from schema"

    def test_output_definition_fields_present(self):
        """All OutputDefinition fields appear in the outputs schema."""
        schema = generate_workflow_schema()
        output_props = schema["properties"]["outputs"]["item_properties"]

        for f in dataclasses.fields(OutputDefinition):
            assert f.name in output_props, f"OutputDefinition.{f.name} missing from schema"

    def test_defaults_fields_present(self):
        """All WorkflowDefaults fields appear in the defaults schema."""
        schema = generate_workflow_schema()
        defaults_props = schema["properties"]["defaults"]["properties"]

        for f in dataclasses.fields(WorkflowDefaults):
            assert f.name in defaults_props, f"WorkflowDefaults.{f.name} missing from schema"

    def test_packages_fields_present(self):
        """All PackagesConfig fields appear in the packages schema."""
        schema = generate_workflow_schema()
        pkg_props = schema["properties"]["packages"]["properties"]

        for f in dataclasses.fields(PackagesConfig):
            assert f.name in pkg_props, f"PackagesConfig.{f.name} missing from schema"


class TestEnumValues:
    """Schema enum values must match actual Python enum members."""

    def test_on_error_values(self):
        """on_error allowed values match OnError enum."""
        schema = generate_workflow_schema()
        defaults_on_error = schema["properties"]["defaults"]["properties"]["on_error"]
        assert set(defaults_on_error["enum"]) == {e.value for e in OnError}

    def test_backoff_values(self):
        """backoff allowed values match BackoffType enum."""
        schema = generate_workflow_schema()
        retry_props = schema["properties"]["defaults"]["properties"]["retry"]["properties"]
        assert set(retry_props["backoff"]["enum"]) == {e.value for e in BackoffType}


class TestTypeMappings:
    """Python type annotations must map to correct JSON Schema types."""

    def test_string_fields(self):
        """str fields map to 'string'."""
        schema = generate_workflow_schema()
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["version"]["type"] == "string"

    def test_integer_fields(self):
        """int fields map to 'integer'."""
        schema = generate_workflow_schema()
        step_props = schema["properties"]["steps"]["items"]["properties"]
        assert step_props["timeout"]["type"] == "integer"

    def test_list_fields(self):
        """list fields map to 'array'."""
        schema = generate_workflow_schema()
        step_props = schema["properties"]["steps"]["items"]["properties"]
        assert step_props["depends_on"]["type"] == "array"

    def test_dict_fields(self):
        """dict fields map to 'object'."""
        schema = generate_workflow_schema()
        step_props = schema["properties"]["steps"]["items"]["properties"]
        assert step_props["params"]["type"] == "object"

    def test_optional_fields_nullable(self):
        """Optional fields are marked as not required."""
        schema = generate_workflow_schema()
        assert "description" not in schema.get("required", [])


class TestSyntaxVariants:
    """Schema must document the multiple accepted syntax forms."""

    def test_inputs_documents_shorthand_forms(self):
        """Inputs schema describes string shorthand and default shorthand."""
        schema = generate_workflow_schema()
        inputs_schema = schema["properties"]["inputs"]
        # Must document the accepted forms
        assert "accepted_forms" in inputs_schema
        forms = inputs_schema["accepted_forms"]
        # At minimum: string shorthand, default shorthand, full dict
        assert len(forms) >= 3

    def test_outputs_documents_both_formats(self):
        """Outputs schema describes both list and dict formats."""
        schema = generate_workflow_schema()
        outputs_schema = schema["properties"]["outputs"]
        assert "accepted_formats" in outputs_schema
        formats = outputs_schema["accepted_formats"]
        assert len(formats) >= 2


class TestExampleValidity:
    """Included example must parse successfully through the real parser."""

    def test_example_parses(self):
        """The example workflow YAML in the schema parses without error."""
        schema = generate_workflow_schema()
        assert "example" in schema
        example_yaml = schema["example"]
        assert isinstance(example_yaml, str)
        # Must parse through the real parser
        workflow = parse_workflow_yaml(example_yaml)
        assert workflow.name is not None
        assert len(workflow.steps) > 0


class TestRoundTrip:
    """Schema describes what the parser actually accepts."""

    def test_minimal_workflow_from_schema(self):
        """A minimal workflow built from schema required fields parses OK."""
        schema = generate_workflow_schema()
        required = schema.get("required", [])
        # name is required (no default); version has a default so not required
        assert "name" in required
        assert "version" in required

        # steps has default_factory=list, so not in required at schema level,
        # but a workflow with no steps is still parseable
        minimal_yaml = """
name: test-minimal
version: "1.0.0"
steps:
  - id: step1
    tool: some_tool
    params:
      key: value
"""
        workflow = parse_workflow_yaml(minimal_yaml)
        assert workflow.name == "test-minimal"
        assert len(workflow.steps) == 1


class TestSchemaGeneratorCodeSteps:
    """Tests for code_steps section in workflow schema."""

    def test_code_steps_key_present(self):
        """workflow_schema output must include code_steps key."""
        schema = generate_workflow_schema()
        assert "code_steps" in schema

    def test_code_steps_has_description(self):
        """code_steps must have a description."""
        schema = generate_workflow_schema()
        assert "description" in schema["code_steps"]
        assert len(schema["code_steps"]["description"]) > 0

    def test_code_steps_documents_result_not_return(self):
        """code_steps must explicitly mention result variable and warn against return."""
        schema = generate_workflow_schema()
        section = str(schema["code_steps"])
        assert "result" in section
        assert "return" in section.lower()  # The warning must mention return

    def test_code_steps_documents_context_api(self):
        """code_steps must document the full context API surface."""
        schema = generate_workflow_schema()
        context_api = schema["code_steps"].get("context_api", {})
        assert "context.inputs" in context_api
        assert "context.steps['step_id'].output" in context_api
        assert "context.config" in context_api
        assert "context.tools.call('tool_name', {...})" in context_api

    def test_code_steps_has_example(self):
        """code_steps must include a concrete example."""
        schema = generate_workflow_schema()
        assert "example" in schema["code_steps"]
        example = schema["code_steps"]["example"]
        assert "result =" in example
        assert "context.steps" in example

    def test_code_steps_has_anti_patterns(self):
        """code_steps must include anti-patterns list to deter return usage."""
        schema = generate_workflow_schema()
        anti_patterns = schema["code_steps"].get("anti_patterns", [])
        assert len(anti_patterns) > 0
        combined = " ".join(anti_patterns)
        assert "return" in combined

    def test_workflow_schema_tool_returns_code_steps(self):
        """code_steps content must be reachable via workflow_schema MCP tool.

        S-290 P2: code_steps is no longer in the default no-arg response
        (Tier 1 minimal schema). Agents reach the sandbox_constraints and
        context_api content via ``workflow_schema(section=...)``. This test
        verifies both routes flow through the MCP tool path.
        """
        from ploston_core.workflow.tools import WorkflowToolsProvider

        provider = WorkflowToolsProvider(MagicMock())

        # context_api section → must include the context.* surface
        ctx_result = asyncio.run(provider.call("workflow_schema", {"section": "context_api"}))
        ctx_schema = json.loads(ctx_result["content"][0]["text"])
        assert ctx_schema["section"] == "context_api"
        assert "context_api" in ctx_schema["schema"]

        # sandbox_constraints section → must include allowed imports
        sb_result = asyncio.run(
            provider.call("workflow_schema", {"section": "sandbox_constraints"})
        )
        sb_schema = json.loads(sb_result["content"][0]["text"])
        assert sb_schema["section"] == "sandbox_constraints"
        assert "allowed_imports" in sb_schema["schema"]


# ──────────────────────────────────────────────────────────────────
# Notification tests for WorkflowToolsProvider
# ──────────────────────────────────────────────────────────────────

VALID_WORKFLOW_YAML = """\
name: test-wf
version: "1.0"
description: test
steps:
  - id: s1
    tool: github__get_file_contents
    input:
      owner: test
      repo: test
"""


class TestWorkflowToolsNotification:
    """Verify _notify_tools_changed fires on create/update/delete."""

    def _make_provider(self, callback=None):
        from ploston_core.workflow.tools import WorkflowToolsProvider

        registry = MagicMock()
        registry.register_from_yaml.return_value = MagicMock(valid=True, errors=[], warnings=[])
        registry.get.return_value = MagicMock(name="test-wf")
        registry.unregister.return_value = True
        return WorkflowToolsProvider(registry, on_tools_changed=callback)

    @pytest.mark.asyncio
    async def test_create_fires_notification(self):
        cb = AsyncMock()
        provider = self._make_provider(cb)
        await provider.call("workflow_create", {"yaml_content": VALID_WORKFLOW_YAML})
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_fires_notification(self):
        cb = AsyncMock()
        provider = self._make_provider(cb)
        await provider.call(
            "workflow_update", {"name": "test-wf", "yaml_content": VALID_WORKFLOW_YAML}
        )
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_fires_notification(self):
        cb = AsyncMock()
        provider = self._make_provider(cb)
        await provider.call("workflow_delete", {"name": "test-wf"})
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_notification_without_callback(self):
        """Provider works fine without on_tools_changed."""
        provider = self._make_provider(None)
        result = await provider.call("workflow_create", {"yaml_content": VALID_WORKFLOW_YAML})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "created"

    @pytest.mark.asyncio
    async def test_schema_does_not_fire_notification(self):
        cb = AsyncMock()
        provider = self._make_provider(cb)
        await provider.call("workflow_schema", {})
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_does_not_fire_notification(self):
        cb = AsyncMock()
        provider = self._make_provider(cb)
        provider._registry.list_workflows.return_value = []
        await provider.call("workflow_list", {})
        cb.assert_not_awaited()


class TestBuildAvailableTools:
    """Verify _build_available_tools handles str and dict tool entries."""

    def _make_provider(self, tool_registry=None, runner_registry=None):
        from ploston_core.workflow.tools import WorkflowToolsProvider

        registry = MagicMock()
        return WorkflowToolsProvider(
            registry,
            tool_registry=tool_registry,
            runner_registry=runner_registry,
        )

    def test_runner_tools_as_strings(self):
        """Runner available_tools as plain strings (mcp__tool)."""
        runner = MagicMock()
        runner.name = "my-runner"
        runner.available_tools = ["github__actions_list", "github__list_commits", "fs__read_file"]

        runner_reg = MagicMock()
        runner_reg.list.return_value = [runner]

        provider = self._make_provider(runner_registry=runner_reg)
        result = provider._build_available_tools()

        # Should group by mcp_server
        by_server = {g["mcp_server"]: g for g in result}
        assert "github" in by_server
        assert sorted(by_server["github"]["tools"]) == ["actions_list", "list_commits"]
        assert by_server["github"]["runner"] == "my-runner"
        assert "fs" in by_server
        assert by_server["fs"]["tools"] == ["read_file"]

    def test_runner_tools_as_dicts(self):
        """Runner available_tools as dicts with name/description/inputSchema."""
        runner = MagicMock()
        runner.name = "my-runner"
        runner.available_tools = [
            {"name": "github__actions_list", "description": "List actions", "inputSchema": {}},
            {"name": "github__list_commits", "description": "List commits", "inputSchema": {}},
            {"name": "fs__read_file", "description": "Read file", "inputSchema": {}},
        ]

        runner_reg = MagicMock()
        runner_reg.list.return_value = [runner]

        provider = self._make_provider(runner_registry=runner_reg)
        result = provider._build_available_tools()

        by_server = {g["mcp_server"]: g for g in result}
        assert "github" in by_server
        assert sorted(by_server["github"]["tools"]) == ["actions_list", "list_commits"]
        assert "fs" in by_server
        assert by_server["fs"]["tools"] == ["read_file"]

    def test_runner_tools_mixed_str_and_dict(self):
        """Runner available_tools can have a mix of str and dict entries."""
        runner = MagicMock()
        runner.name = "my-runner"
        runner.available_tools = [
            "github__actions_list",
            {"name": "fs__read_file", "description": "Read", "inputSchema": {}},
        ]

        runner_reg = MagicMock()
        runner_reg.list.return_value = [runner]

        provider = self._make_provider(runner_registry=runner_reg)
        result = provider._build_available_tools()

        by_server = {g["mcp_server"]: g for g in result}
        assert "github" in by_server
        assert "fs" in by_server

    def test_no_registries(self):
        """Returns empty when neither registry is provided."""
        provider = self._make_provider()
        assert provider._build_available_tools() == []
