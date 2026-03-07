"""Unit tests for workflow schema generator.

Tests that the generated schema is complete, accurate, and stays in sync
with the actual dataclass definitions (single source of truth).
"""

import dataclasses

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
