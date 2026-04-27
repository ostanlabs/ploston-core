"""Unit tests for the missing-await static check (S-286 / T-905).

Covers ``_check_missing_await`` and its surfacing through
``_handle_validate``. The check is AST-based and best-effort: warnings are
advisory and never affect ``valid``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ploston_core.workflow.tools import (
    WorkflowToolsProvider,
    _check_missing_await,
)
from ploston_core.workflow.types import StepDefinition


def _parse(mcp_response: dict) -> dict:
    return json.loads(mcp_response["content"][0]["text"])


def _step(step_id: str, code: str) -> StepDefinition:
    return StepDefinition(id=step_id, code=code)


class TestMissingAwaitCheck:
    def test_call_mcp_without_await_warns(self):
        warnings = _check_missing_await([_step("s1", 'x = context.tools.call_mcp("a", "b", {})')])
        assert len(warnings) == 1
        w = warnings[0]
        assert w["path"] == "steps[s1].code"
        assert w["line"] == 1
        assert "await" in w["message"]

    def test_call_without_await_warns(self):
        warnings = _check_missing_await([_step("s1", 'x = context.tools.call("a", {})')])
        assert len(warnings) == 1
        assert warnings[0]["path"] == "steps[s1].code"

    def test_awaited_call_no_warning(self):
        warnings = _check_missing_await(
            [_step("s1", 'x = await context.tools.call_mcp("a", "b", {})')]
        )
        assert warnings == []

    def test_no_code_steps_no_warning(self):
        # Steps without ``code`` are skipped (tool steps).
        warnings = _check_missing_await([StepDefinition(id="s1", tool="python_exec", mcp="system")])
        assert warnings == []

    def test_multiple_missing_awaits(self):
        code = 'a = context.tools.call("x", {})\nb = context.tools.call_mcp("y", "z", {})'
        warnings = _check_missing_await([_step("s1", code)])
        assert len(warnings) == 2
        assert {w["line"] for w in warnings} == {1, 2}

    def test_call_in_string_literal_no_warning(self):
        warnings = _check_missing_await([_step("s1", 'x = "context.tools.call_mcp(...)"')])
        assert warnings == []

    def test_call_in_comment_no_warning(self):
        warnings = _check_missing_await([_step("s1", "# context.tools.call_mcp(\nresult = {}")])
        assert warnings == []

    def test_syntax_error_in_code_skipped(self):
        warnings = _check_missing_await([_step("s1", "this is not python")])
        assert warnings == []

    def test_multiline_await_no_warning(self):
        code = 'x = await context.tools.call_mcp(\n  "a", "b", {}\n)'
        warnings = _check_missing_await([_step("s1", code)])
        assert warnings == []

    def test_warning_shape(self):
        warnings = _check_missing_await([_step("s1", 'x = context.tools.call_mcp("a", "b", {})')])
        assert set(warnings[0].keys()) == {"path", "line", "message"}
        assert isinstance(warnings[0]["path"], str)
        assert isinstance(warnings[0]["line"], int)
        assert isinstance(warnings[0]["message"], str)

    def test_unrelated_call_no_warning(self):
        # Bare ``call_mcp(...)`` with a different attribute chain must not match.
        warnings = _check_missing_await([_step("s1", 'x = client.call_mcp("a", "b", {})')])
        assert warnings == []


class TestHandleValidateSurfacesAwaitWarning:
    @pytest.fixture
    def provider(self):
        reg = MagicMock()
        valid_result = MagicMock()
        valid_result.valid = True
        valid_result.errors = []
        valid_result.warnings = []
        reg.validate_yaml.return_value = valid_result
        return WorkflowToolsProvider(workflow_registry=reg)

    @pytest.mark.asyncio
    async def test_validate_returns_await_warning(self, provider):
        yaml_content = (
            "name: dx_check\n"
            'version: "1.0"\n'
            'description: "DX await regression check workflow"\n'
            "steps:\n"
            "  - id: bad\n"
            "    code: |\n"
            '      x = context.tools.call_mcp("a", "b", {})\n'
            '      result = {"x": x}\n'
        )
        raw = await provider.call("workflow_validate", {"yaml_content": yaml_content})
        result = _parse(raw)
        assert result["valid"] is True  # warning never blocks
        await_warnings = [w for w in result["warnings"] if w.get("path") == "steps[bad].code"]
        assert len(await_warnings) == 1
        assert await_warnings[0]["line"] == 1
        assert "await" in await_warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_validate_no_warning_when_awaited(self, provider):
        yaml_content = (
            "name: dx_check\n"
            'version: "1.0"\n'
            'description: "DX await regression check workflow"\n'
            "steps:\n"
            "  - id: ok\n"
            "    code: |\n"
            '      x = await context.tools.call_mcp("a", "b", {})\n'
            '      result = {"x": x}\n'
        )
        raw = await provider.call("workflow_validate", {"yaml_content": yaml_content})
        result = _parse(raw)
        await_warnings = [w for w in result["warnings"] if w.get("path") == "steps[ok].code"]
        assert await_warnings == []
