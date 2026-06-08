"""Spec-coverage tests for WorkflowValidator.validate and helpers.

Asserts the documented validation contract (validator.py docstring + DEC-157
tool resolution, H-1a `when` operator guard, DEC-002 template checks):

  - required fields (name/version)
  - tool XOR code per step (both / neither)
  - mcp required for tool steps
  - tool resolution: CP-direct hit/miss, runner-hosted hit/miss, runner
    inference (single / ambiguous), unknown server hint
  - depends_on reference + circular-dependency detection
  - template syntax + unknown inputs/steps references
  - `when` expression syntax + unsupported-operator guard
  - output from_path XOR value
  - duplicate step IDs

External boundaries (ToolRegistry, RunnerRegistry) are mocked; the validator
logic runs for real.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ploston_core.workflow.types import (
    InputDefinition,
    OutputDefinition,
    StepDefinition,
    WorkflowDefaults,
    WorkflowDefinition,
)
from ploston_core.workflow.validator import (
    WorkflowValidator,
    _unsupported_when_operator,
)

# ── Tool-registry mock helpers ───────────────────────────────────────


def _tool_registry(tools: list[tuple[str, str]] | None = None) -> MagicMock:
    """Mock ToolRegistry whose list_tools(server_name=...) filters by server.

    ``tools`` is a list of (name, server_name) pairs.
    """
    tools = tools or [("echo", "system")]
    tool_objs = []
    for name, server in tools:
        t = MagicMock()
        t.name = name
        t.server_name = server
        tool_objs.append(t)

    def _list_tools(server_name=None):
        if server_name is None:
            return tool_objs
        return [t for t in tool_objs if t.server_name == server_name]

    tr = MagicMock()
    tr.list_tools.side_effect = _list_tools
    return tr


def _runner(name: str, status: str, tool_names: list[str]) -> MagicMock:
    r = MagicMock()
    r.name = name
    r.status.value = status
    r.available_tools = list(tool_names)
    return r


def _runner_registry(runners: list[MagicMock]) -> MagicMock:
    rr = MagicMock()
    rr.list.return_value = runners
    rr._get_tool_name.side_effect = lambda t: t  # tool entries are plain strings

    def _get_by_name(name):
        for r in runners:
            if r.name == name:
                return r
        return None

    rr.get_by_name.side_effect = _get_by_name

    def _has_tool(runner_name, canonical):
        r = _get_by_name(runner_name)
        return r is not None and canonical in r.available_tools

    rr.has_tool.side_effect = _has_tool
    return rr


def _validator(tr=None, rr=None) -> WorkflowValidator:
    return WorkflowValidator(tr or _tool_registry(), runner_registry=rr)


def _errs(result) -> list[tuple[str, str]]:
    return [(e.path, e.message) for e in result.errors]


# ── required fields ──────────────────────────────────────────────────


class TestRequiredFields:
    def test_missing_name(self):
        wf = WorkflowDefinition(name="", version="1.0.0", steps=[])
        result = _validator().validate(wf)
        assert not result.valid
        assert any(p == "name" for p, _ in _errs(result))

    def test_missing_version(self):
        wf = WorkflowDefinition(name="x", version="", steps=[])
        result = _validator().validate(wf)
        assert not result.valid
        assert any(p == "version" for p, _ in _errs(result))

    def test_minimal_valid_workflow(self):
        wf = WorkflowDefinition(
            name="x", version="1.0.0", steps=[StepDefinition(id="s1", code="result = 1")]
        )
        result = _validator().validate(wf)
        assert result.valid, _errs(result)


# ── tool XOR code ────────────────────────────────────────────────────


class TestToolXorCode:
    def test_both_tool_and_code_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="echo", mcp="system", code="result = 1")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any("not both" in m and p == "steps.s1" for p, m in _errs(result))

    def test_neither_tool_nor_code_rejected(self):
        wf = WorkflowDefinition(name="x", version="1.0.0", steps=[StepDefinition(id="s1")])
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1" and "Step must have either 'tool' or 'code'" == m
            for p, m in _errs(result)
        )


# ── mcp required for tool steps ──────────────────────────────────────


class TestMcpRequired:
    def test_tool_step_without_mcp_rejected(self):
        wf = WorkflowDefinition(
            name="x", version="1.0.0", steps=[StepDefinition(id="s1", tool="echo")]
        )
        result = _validator().validate(wf)
        assert not result.valid
        paths = [p for p, _ in _errs(result)]
        assert "steps.s1.mcp" in paths
        # The mcp message explains the field is required.
        assert any(p == "steps.s1.mcp" and "required for tool steps" in m for p, m in _errs(result))


# ── CP-direct tool resolution ────────────────────────────────────────


class TestCpDirectResolution:
    def test_known_tool_resolves(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="echo", mcp="system")],
        )
        result = _validator().validate(wf)
        assert result.valid, _errs(result)

    def test_unknown_tool_on_known_server_lists_available(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="nope", mcp="system")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        msg = next(m for p, m in _errs(result) if p == "steps.s1.tool")
        assert "Tool 'nope' not found on MCP server 'system'" in msg
        # Hint lists available tool names on that server.
        assert "echo" in msg

    def test_unknown_server_lists_known_servers(self):
        tr = _tool_registry([("echo", "system"), ("read", "files")])
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="echo", mcp="ghostserver")],
        )
        result = _validator(tr=tr).validate(wf)
        assert not result.valid
        msg = next(m for p, m in _errs(result) if p == "steps.s1.tool")
        assert "No MCP server named 'ghostserver'" in msg
        # Known servers are surfaced.
        assert "files" in msg and "system" in msg

    def test_check_tools_false_skips_resolution(self):
        # tool doesn't exist, but check_tools=False → no resolution error.
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="nope", mcp="ghost")],
        )
        result = _validator().validate(wf, check_tools=False)
        assert result.valid, _errs(result)


# ── runner-hosted tool resolution (DEC-157) ──────────────────────────


class TestRunnerResolution:
    def test_explicit_runner_hosts_tool(self):
        rr = _runner_registry([_runner("r1", "connected", ["srv__do", "r1__srv__do"])])
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            defaults=WorkflowDefaults(runner="r1"),
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(rr=rr).validate(wf)
        assert result.valid, _errs(result)

    def test_explicit_runner_missing_tool_lists_available(self):
        rr = _runner_registry([_runner("r1", "connected", ["srv__other"])])
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            defaults=WorkflowDefaults(runner="r1"),
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(rr=rr).validate(wf)
        assert not result.valid
        msg = next(m for p, m in _errs(result) if p == "steps.s1.tool")
        assert "not found on MCP server 'srv'" in msg
        assert "runner 'r1'" in msg
        # Hint lists tools on that server for the runner.
        assert "srv__other" in msg

    def test_explicit_runner_not_in_registry(self):
        rr = _runner_registry([])  # r1 not present
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            defaults=WorkflowDefaults(runner="r1"),
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(rr=rr).validate(wf)
        assert not result.valid
        msg = next(m for p, m in _errs(result) if p == "steps.s1.tool")
        assert "Runner 'r1' not found in registry." in msg

    def test_single_runner_inference(self):
        """No explicit runner, exactly one connected runner hosts the mcp."""
        rr = _runner_registry([_runner("r1", "connected", ["srv__do", "r1__srv__do"])])
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(rr=rr).validate(wf)
        assert result.valid, _errs(result)

    def test_disconnected_runner_not_inferred(self):
        """A disconnected runner hosting the mcp is ignored for inference;
        falls through to CP-direct (which lacks the tool) → error."""
        rr = _runner_registry([_runner("r1", "disconnected", ["srv__do", "r1__srv__do"])])
        tr = _tool_registry([("echo", "system")])  # no 'do' on 'srv'
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(tr=tr, rr=rr).validate(wf)
        assert not result.valid
        assert any(p == "steps.s1.tool" for p, _ in _errs(result))

    def test_ambiguous_runner_inference_requires_default(self):
        rr = _runner_registry(
            [
                _runner("r1", "connected", ["srv__do"]),
                _runner("r2", "connected", ["srv__do"]),
            ]
        )
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", tool="do", mcp="srv")],
        )
        result = _validator(rr=rr).validate(wf)
        assert not result.valid
        msg = next(m for p, m in _errs(result) if p == "steps.s1.tool")
        assert "multiple runners" in msg
        assert "r1" in msg and "r2" in msg
        assert "defaults.runner" in msg


# ── depends_on + circular dependencies ───────────────────────────────


class TestDependencies:
    def test_unknown_dependency_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(id="s1", code="result = 1", depends_on=["ghost"]),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1.depends_on" and "Dependency 'ghost' not found" == m
            for p, m in _errs(result)
        )

    def test_valid_dependency_ok(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(id="s1", code="result = 1"),
                StepDefinition(id="s2", code="result = 2", depends_on=["s1"]),
            ],
        )
        result = _validator().validate(wf)
        assert result.valid, _errs(result)

    def test_circular_dependency_detected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(id="s1", code="result = 1", depends_on=["s2"]),
                StepDefinition(id="s2", code="result = 2", depends_on=["s1"]),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(p == "steps" and "Circular dependency" in m for p, m in _errs(result))


# ── duplicate step IDs ───────────────────────────────────────────────


class TestDuplicateSteps:
    def test_duplicate_ids_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(id="dup", code="result = 1"),
                StepDefinition(id="dup", code="result = 2"),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps" and "Duplicate step IDs" in m and "dup" in m for p, m in _errs(result)
        )


# ── template references in params ─────────────────────────────────────


class TestTemplateReferences:
    def test_unknown_input_reference_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            inputs=[InputDefinition(name="known", type="string")],
            steps=[
                StepDefinition(
                    id="s1",
                    tool="echo",
                    mcp="system",
                    params={"msg": "{{ inputs.unknown }}"},
                ),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1.params" and "unknown input 'unknown'" in m for p, m in _errs(result)
        )

    def test_known_input_reference_ok(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            inputs=[InputDefinition(name="known", type="string")],
            steps=[
                StepDefinition(
                    id="s1",
                    tool="echo",
                    mcp="system",
                    params={"msg": "{{ inputs.known }}"},
                ),
            ],
        )
        result = _validator().validate(wf)
        assert result.valid, _errs(result)

    def test_unknown_step_reference_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(
                    id="s1",
                    tool="echo",
                    mcp="system",
                    params={"v": "{{ steps.ghoststep.output }}"},
                ),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1.params" and "unknown step 'ghoststep'" in m for p, m in _errs(result)
        )

    def test_template_syntax_error_rejected(self):
        # The restricted engine (DEC-002) rejects function calls inside a
        # template expression; this is reported as a params template error.
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[
                StepDefinition(
                    id="s1",
                    tool="echo",
                    mcp="system",
                    params={"v": "{{ foo(bar) }}"},
                ),
            ],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(p == "steps.s1.params" and "Template error" in m for p, m in _errs(result))


# ── `when` expression validation + operator guard ────────────────────


class TestWhenGuard:
    @pytest.mark.parametrize(
        "expr,op",
        [
            ("a == b", "=="),
            ("x != y", "!="),
            ("a <= b", "<="),
            ("a >= b", ">="),
            ("a < b", "<"),
            ("a > b", ">"),
            ("a + b", "+"),
            ("a - b", "-"),
            ("a * b", "*"),
            ("a / b", "/"),
            ("a % b", "%"),
            ("a and b", "and"),
            ("a or b", "or"),
            ("not a", "not"),
            ("a in b", "in"),
            ("a is b", "is"),
        ],
    )
    def test_unsupported_operator_helper(self, expr, op):
        assert _unsupported_when_operator(expr) == op

    def test_plain_variable_path_supported(self):
        assert _unsupported_when_operator("steps.check.output.ok") is None

    def test_filter_args_not_treated_as_operators(self):
        # default(0) / round(2) live after the pipe; their parens/digits must
        # not be flagged as arithmetic operators.
        assert _unsupported_when_operator("steps.s.output.n | default(0)") is None

    def test_validate_rejects_when_with_operator(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1", when="a == b")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1.when" and "Unsupported operator '=='" in m for p, m in _errs(result)
        )

    def test_when_template_syntax_error_reported(self):
        # A function call in `when` is a template *syntax* error (distinct from
        # the operator guard) and surfaces under steps.<id>.when.
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1", when="foo(bar)")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "steps.s1.when" and "Template error in when expression" in m
            for p, m in _errs(result)
        )

    def test_validate_accepts_plain_when(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1", when="steps.s0.output.ok")],
        )
        # s0 doesn't exist but `when` refs aren't reference-checked, only
        # syntax + operator-guarded. So this is valid.
        result = _validator().validate(wf)
        assert result.valid, _errs(result)


# ── outputs from_path XOR value ──────────────────────────────────────


class TestOutputs:
    def test_output_with_both_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1")],
            outputs=[OutputDefinition(name="o", from_path="steps.s1.output", value="{{ x }}")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(p == "outputs.o" and "not both" in m for p, m in _errs(result))

    def test_output_with_neither_rejected(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1")],
            outputs=[OutputDefinition(name="o")],
        )
        result = _validator().validate(wf)
        assert not result.valid
        assert any(
            p == "outputs.o" and m == "Output must have either 'from_path' or 'value'"
            for p, m in _errs(result)
        )

    def test_output_with_from_path_only_ok(self):
        wf = WorkflowDefinition(
            name="x",
            version="1.0.0",
            steps=[StepDefinition(id="s1", code="result = 1")],
            outputs=[OutputDefinition(name="o", from_path="steps.s1.output")],
        )
        result = _validator().validate(wf)
        assert result.valid, _errs(result)
