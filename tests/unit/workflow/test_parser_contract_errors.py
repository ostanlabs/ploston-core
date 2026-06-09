"""Tests for workflow parser contract errors (Finding L-2).

The public parse entrypoint must raise the structured contract error
``AELError(INPUT_INVALID)`` for malformed workflow input -- invalid enum
values and missing required keys -- rather than leaking raw Python
``ValueError`` / ``KeyError``.
"""

import pytest

from ploston_core.errors import AELError
from ploston_core.workflow.parser import parse_workflow_yaml


def _assert_input_invalid(yaml_content: str) -> AELError:
    """Parse and assert it raises AELError(INPUT_INVALID), returning the error."""
    with pytest.raises(AELError) as exc_info:
        parse_workflow_yaml(yaml_content)
    assert exc_info.value.code == "INPUT_INVALID"
    return exc_info.value


# ---------------------------------------------------------------------------
# Missing required keys (raw KeyError leak)
# ---------------------------------------------------------------------------


def test_step_missing_id_raises_input_invalid():
    """A step with no `id` raises INPUT_INVALID, not a bare KeyError."""
    yaml_content = """
name: test-workflow
steps:
  - code: "pass"
"""
    err = _assert_input_invalid(yaml_content)
    assert "id" in (err.detail or "")


def test_list_output_missing_name_raises_input_invalid():
    """A list-form output with no `name` raises INPUT_INVALID, not KeyError."""
    yaml_content = """
name: test-workflow
steps:
  - id: x
    code: "pass"
outputs:
  - from: steps.x.output.title
"""
    err = _assert_input_invalid(yaml_content)
    assert "name" in (err.detail or "")


# ---------------------------------------------------------------------------
# Invalid enum values (raw ValueError leak)
# ---------------------------------------------------------------------------


def test_step_invalid_on_error_raises_input_invalid():
    """A step with an invalid `on_error` enum raises INPUT_INVALID."""
    yaml_content = """
name: test-workflow
steps:
  - id: x
    code: "pass"
    on_error: failx
"""
    err = _assert_input_invalid(yaml_content)
    assert "failx" in (err.detail or "")
    assert "on_error" in (err.detail or "")


def test_defaults_invalid_on_error_raises_input_invalid():
    """Defaults with an invalid `on_error` enum raises INPUT_INVALID."""
    yaml_content = """
name: test-workflow
defaults:
  on_error: bogus
steps:
  - id: x
    code: "pass"
"""
    err = _assert_input_invalid(yaml_content)
    assert "bogus" in (err.detail or "")


def test_step_invalid_on_missing_tool_raises_input_invalid():
    """A step with an invalid `on_missing_tool` enum raises INPUT_INVALID."""
    yaml_content = """
name: test-workflow
steps:
  - id: x
    code: "pass"
    on_missing_tool: nope
"""
    err = _assert_input_invalid(yaml_content)
    assert "nope" in (err.detail or "")


def test_step_invalid_retry_backoff_raises_input_invalid():
    """A step retry with an invalid `backoff` enum raises INPUT_INVALID."""
    yaml_content = """
name: test-workflow
steps:
  - id: x
    code: "pass"
    retry:
      backoff: wiggle
"""
    err = _assert_input_invalid(yaml_content)
    assert "wiggle" in (err.detail or "")


def test_defaults_invalid_retry_backoff_raises_input_invalid():
    """Defaults retry with an invalid `backoff` enum raises INPUT_INVALID."""
    yaml_content = """
name: test-workflow
defaults:
  retry:
    backoff: zigzag
steps:
  - id: x
    code: "pass"
"""
    err = _assert_input_invalid(yaml_content)
    assert "zigzag" in (err.detail or "")
