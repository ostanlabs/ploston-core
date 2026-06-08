"""Tests for workflow YAML parser (H-11 bug fixes).

Covers:
- normalize_inputs() record-form vs map-form disambiguation.
- list-format outputs honoring the `from` key.
"""

from ploston_core.workflow.parser import normalize_inputs, parse_workflow_yaml
from ploston_core.workflow.types import InputDefinition, OutputDefinition

# ---------------------------------------------------------------------------
# BUG 1: normalize_inputs() record-form disambiguation
# ---------------------------------------------------------------------------


def test_record_form_input_single_definition():
    """A record-form dict with a non-dict `name` produces exactly ONE input."""
    result = normalize_inputs([{"name": "url", "type": "string", "required": True}])

    assert result == [
        InputDefinition(name="url", type="string", required=True),
    ]
    assert len(result) == 1


def test_record_form_input_with_default():
    """Record-form with a default: required defaults False, type defaults string."""
    result = normalize_inputs([{"name": "url", "default": "http://x"}])

    assert len(result) == 1
    inp = result[0]
    assert inp.name == "url"
    assert inp.type == "string"
    assert inp.required is False
    assert inp.default == "http://x"


def test_record_form_input_no_default_required_true():
    """Record-form without default and without explicit required -> required True."""
    result = normalize_inputs([{"name": "url", "type": "int"}])

    assert len(result) == 1
    inp = result[0]
    assert inp.name == "url"
    assert inp.type == "int"
    assert inp.required is True
    assert inp.default is None


def test_single_key_map_full_definition():
    """Single-key map {url: {type: int}} -> one input named url of type int."""
    result = normalize_inputs([{"url": {"type": "int"}}])

    assert len(result) == 1
    inp = result[0]
    assert inp.name == "url"
    assert inp.type == "int"


def test_single_key_scalar_default():
    """Single-key scalar {url: "d"} -> one input url, default d, not required."""
    result = normalize_inputs([{"url": "d"}])

    assert len(result) == 1
    inp = result[0]
    assert inp.name == "url"
    assert inp.default == "d"
    assert inp.required is False


def test_escape_hatch_name_maps_to_dict():
    """Escape hatch {name: {type: string}} -> input literally named 'name'."""
    result = normalize_inputs([{"name": {"type": "string"}}])

    assert len(result) == 1
    inp = result[0]
    assert inp.name == "name"
    assert inp.type == "string"


def test_bare_string_input():
    """Bare string item -> required input with that name."""
    result = normalize_inputs(["url"])

    assert result == [InputDefinition(name="url", required=True)]


# ---------------------------------------------------------------------------
# BUG 2: list-format outputs honor the `from` key
# ---------------------------------------------------------------------------


def _minimal_workflow_yaml(outputs_block: str) -> str:
    return f"""
name: test-workflow
steps:
  - id: x
    code: "pass"
{outputs_block}
"""


def test_list_output_from_key():
    """List-form output using `from` populates from_path."""
    yaml_content = _minimal_workflow_yaml(
        """outputs:
  - name: title
    from: steps.x.output.title
"""
    )
    wf = parse_workflow_yaml(yaml_content)

    assert wf.outputs == [
        OutputDefinition(name="title", from_path="steps.x.output.title"),
    ]


def test_list_output_from_path_key_still_works():
    """List-form output using `from_path` still populates from_path."""
    yaml_content = _minimal_workflow_yaml(
        """outputs:
  - name: title
    from_path: steps.x.output.title
"""
    )
    wf = parse_workflow_yaml(yaml_content)

    assert len(wf.outputs) == 1
    assert wf.outputs[0].name == "title"
    assert wf.outputs[0].from_path == "steps.x.output.title"


def test_dict_output_from_key_still_works():
    """Dict-form output using `from` still populates from_path."""
    yaml_content = _minimal_workflow_yaml(
        """outputs:
  title:
    from: steps.x.output.title
"""
    )
    wf = parse_workflow_yaml(yaml_content)

    assert len(wf.outputs) == 1
    assert wf.outputs[0].name == "title"
    assert wf.outputs[0].from_path == "steps.x.output.title"
