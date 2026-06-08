"""Strict-TDD tests for H-1 (`when` footguns) and H-3 (falsy outputs dropped).

H-3: a workflow output whose value template renders to a falsy value
     (0, "", False, [], {}) must appear in outputs verbatim, not None.

H-1a: a `when` expression containing operators the restricted template
      engine cannot evaluate (==, and, etc.) must FAIL validation with
      INPUT_INVALID, rather than passing validation and blowing up with
      TEMPLATE_ERROR at runtime.

H-1b: at runtime a `when` that renders to the string "false"/"0"/"no"/""
      must SKIP the step; "true"/"1"/"yes" must RUN it; a real boolean
      True still runs (existing truthiness contract preserved).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.engine.types import ExecutionContext
from ploston_core.template import TemplateEngine
from ploston_core.types import StepStatus
from ploston_core.workflow.types import (
    OutputDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from ploston_core.workflow.validator import WorkflowValidator

# ─────────────────────────────────────────────────────────────────────
# H-3 — falsy outputs must not be dropped (engine._compute_outputs)
# ─────────────────────────────────────────────────────────────────────


def _real_engine() -> WorkflowEngine:
    """Engine wired with the real TemplateEngine (others mocked)."""
    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        template_engine=TemplateEngine(),
        config=MagicMock(default_timeout=30),
    )


def _ctx(workflow: WorkflowDefinition, inputs: dict) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        workflow=workflow,
        inputs=inputs,
        config={},
    )


@pytest.mark.parametrize(
    "rendered_template, expected",
    [
        ("{{ inputs.zero }}", 0),
        ("{{ inputs.empty }}", ""),
        ("{{ inputs.flag }}", False),
        ("{{ inputs.empty_list }}", []),
        ("{{ inputs.empty_dict }}", {}),
    ],
)
def test_falsy_outputs_are_preserved(rendered_template, expected) -> None:
    engine = _real_engine()
    workflow = WorkflowDefinition(
        name="wf",
        version="1.0.0",
        outputs=[OutputDefinition(name="result", value=rendered_template)],
    )
    inputs = {
        "zero": 0,
        "empty": "",
        "flag": False,
        "empty_list": [],
        "empty_dict": {},
    }
    outputs = engine._compute_outputs(workflow, _ctx(workflow, inputs))
    assert outputs["result"] == expected
    assert outputs["result"] is not None


def test_literal_empty_string_output_is_not_dropped() -> None:
    """An output whose ``value`` is the literal empty string must render to
    "" — not be routed to the ``else: value = None`` branch by the
    ``elif output_def.value:`` truthiness check."""
    engine = _real_engine()
    workflow = WorkflowDefinition(
        name="wf",
        version="1.0.0",
        outputs=[OutputDefinition(name="result", value="")],
    )
    outputs = engine._compute_outputs(workflow, _ctx(workflow, {}))
    assert outputs["result"] == ""
    assert outputs["result"] is not None


def test_truthy_output_still_works() -> None:
    engine = _real_engine()
    workflow = WorkflowDefinition(
        name="wf",
        version="1.0.0",
        outputs=[OutputDefinition(name="result", value="{{ inputs.n }}")],
    )
    outputs = engine._compute_outputs(workflow, _ctx(workflow, {"n": 42}))
    assert outputs["result"] == 42


# ─────────────────────────────────────────────────────────────────────
# H-1a — `when` with unsupported operators fails VALIDATION (not runtime)
# ─────────────────────────────────────────────────────────────────────


def _validator() -> WorkflowValidator:
    return WorkflowValidator(tool_registry=MagicMock(list_tools=lambda **_: []))


def _wf_with_when(when_expr: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="wf",
        version="1.0.0",
        steps=[StepDefinition(id="s1", code="result = 1", when=when_expr)],
    )


@pytest.mark.parametrize(
    "when_expr",
    [
        "inputs.a == inputs.b",
        "inputs.a != 1",
        "inputs.a < 5",
        "inputs.a > 5",
        "inputs.a <= 5",
        "inputs.a >= 5",
        "inputs.a and inputs.b",
        "inputs.a or inputs.b",
        "not inputs.a",
        "inputs.a in inputs.b",
        "inputs.a is None",
        "inputs.a + 1",
        "inputs.a - 1",
        "inputs.a * 2",
        "inputs.a / 2",
        "inputs.a % 2",
    ],
)
def test_when_with_unsupported_operators_fails_validation(when_expr) -> None:
    validator = _validator()
    result = validator.validate(_wf_with_when(when_expr), check_tools=False)
    assert not result.valid, f"expected validation failure for: {when_expr}"
    when_errors = [e for e in result.errors if e.path == "steps.s1.when"]
    assert when_errors, f"expected a .when error for: {when_expr}"
    # Clear, actionable message pointing at a code-step precompute.
    assert any(
        "precompute" in e.message.lower() or "unsupported" in e.message.lower() for e in when_errors
    )


@pytest.mark.parametrize(
    "when_expr",
    [
        "inputs.flag",
        "steps.s0.output.ready",
        "inputs.items | length",
        "inputs.value | default(0)",
    ],
)
def test_when_with_simple_paths_and_filters_passes_validation(when_expr) -> None:
    validator = _validator()
    wf = WorkflowDefinition(
        name="wf",
        version="1.0.0",
        inputs=[],
        steps=[
            StepDefinition(id="s0", code="result = 1"),
            StepDefinition(id="s1", code="result = 2", when=when_expr),
        ],
    )
    result = validator.validate(wf, check_tools=False)
    when_errors = [e for e in result.errors if e.path == "steps.s1.when"]
    assert not when_errors, f"unexpected .when error for: {when_expr}: {when_errors}"


# ─────────────────────────────────────────────────────────────────────
# H-1b — string-boolean coercion in the runtime `when` eval
# ─────────────────────────────────────────────────────────────────────


async def _run_single_step(when_expr: str, inputs: dict) -> StepStatus:
    """Execute a one-step workflow and return the step's status."""
    engine = WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        template_engine=TemplateEngine(),
        config=MagicMock(default_timeout=30),
    )

    # Stub the actual step execution so we only observe skip/run decisions.
    async def _fake_exec_code(step, context):  # noqa: ANN001
        return ("ran", [])

    engine._execute_code_step = _fake_exec_code  # type: ignore[assignment]

    workflow = WorkflowDefinition(
        name="wf",
        version="1.0.0",
        steps=[StepDefinition(id="s1", code="result = 1", when=when_expr)],
    )
    result = await engine.execute_workflow(workflow, inputs)
    return result.steps[0].status


@pytest.mark.parametrize("falsy", ["false", "False", "FALSE", " false ", "0", "no", ""])
@pytest.mark.asyncio
async def test_when_string_false_skips(falsy) -> None:
    status = await _run_single_step("inputs.cond", {"cond": falsy})
    assert status == StepStatus.SKIPPED, f"{falsy!r} should be treated as falsy"


@pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "anything"])
@pytest.mark.asyncio
async def test_when_string_true_runs(truthy) -> None:
    status = await _run_single_step("inputs.cond", {"cond": truthy})
    assert status == StepStatus.COMPLETED, f"{truthy!r} should be treated as truthy"


@pytest.mark.asyncio
async def test_when_real_bool_true_runs() -> None:
    status = await _run_single_step("inputs.flag", {"flag": True})
    assert status == StepStatus.COMPLETED


@pytest.mark.asyncio
async def test_when_real_bool_false_skips() -> None:
    status = await _run_single_step("inputs.flag", {"flag": False})
    assert status == StepStatus.SKIPPED
