"""S-292 P4d: tests for WorkflowEngine._build_error_metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ploston_core.engine.engine import WorkflowEngine
from ploston_core.engine.types import ExecutionContext, StepResult
from ploston_core.errors import create_error
from ploston_core.types import StepStatus, StepType


@pytest.fixture
def engine() -> WorkflowEngine:
    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        template_engine=MagicMock(),
        config=MagicMock(step_timeout=30),
    )


def _engine_with_tools(tool_names: list[str]) -> WorkflowEngine:
    registry = MagicMock()
    tools = []
    for n in tool_names:
        t = MagicMock()
        t.name = n
        tools.append(t)
    registry.list_tools.return_value = tools
    return WorkflowEngine(
        workflow_registry=MagicMock(),
        tool_invoker=MagicMock(),
        template_engine=MagicMock(),
        config=MagicMock(step_timeout=30),
        tool_registry=registry,
    )


def _ctx_with_prior_outputs() -> ExecutionContext:
    ctx = ExecutionContext(
        execution_id="exec-1",
        workflow=MagicMock(name="wf"),
        inputs={},
        config={},
    )
    ctx.step_results["fetch"] = StepResult(
        step_id="fetch",
        status=StepStatus.COMPLETED,
        duration_ms=10,
        output={"items": [1, 2], "total": 2},
    )
    return ctx


# ── code step failure ──


def test_code_failure_emits_line_and_code_context(engine: WorkflowEngine) -> None:
    code = "a = 1\nb = 2\nz = 1 / 0\n"
    step = MagicMock()
    step.id = "diagnose"
    step.step_type = StepType.CODE
    step.code = code
    step.tool = None
    step.mcp = None

    try:
        exec(compile(code, "<step:diagnose>", "exec"))
    except Exception as exc:
        meta = engine._build_error_metadata(
            step=step,
            exc=exc,
            rendered_params={},
            context=_ctx_with_prior_outputs(),
        )

    assert meta["step_type"] == "code"
    assert meta["step_id"] == "diagnose"
    assert meta["exception_type"] == "ZeroDivisionError"
    assert meta["line_in_step"] == 3
    assert meta["source_line"] == "z = 1 / 0"
    err_lines = [c for c in meta["code_context"] if c.get("is_error_line")]
    assert err_lines and err_lines[0]["line"] == 3
    assert meta["fix_via"] == "workflow_patch"
    assert meta["step_inputs"]["prior_step_output_keys"] == {"fetch": ["items", "total"]}


# ── tool step failure ──


def test_tool_failure_emits_params_sent_and_skips_code_context(engine: WorkflowEngine) -> None:
    step = MagicMock()
    step.id = "fetch"
    step.step_type = StepType.TOOL
    step.tool = "github.list_commits"
    step.mcp = "github"
    step.code = None

    rendered = {"owner": "acme", "repo": "widgets", "limit": 10}
    meta = engine._build_error_metadata(
        step=step,
        exc=RuntimeError("rate limit"),
        rendered_params=rendered,
        context=_ctx_with_prior_outputs(),
    )

    assert meta["step_type"] == "tool"
    assert meta["tool"] == "github.list_commits"
    assert meta["mcp"] == "github"
    assert meta["params_sent"] == rendered
    assert "code_context" not in meta
    assert "line_in_step" not in meta


# ── empty prior outputs ──


def test_tool_typo_yields_suggested_fix() -> None:
    engine = _engine_with_tools(["actions_list", "list_commits", "create_issue"])
    step = MagicMock()
    step.id = "fetch"
    step.step_type = StepType.TOOL
    step.tool = "actions_lis"
    step.mcp = "github"
    step.code = None

    err = create_error("TOOL_UNAVAILABLE", tool_name="actions_lis")
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    meta = engine._build_error_metadata(step=step, exc=err, rendered_params={}, context=ctx)

    assert meta["suggested_fix"] == {
        "op": "set",
        "path": "steps.fetch.tool",
        "value": "actions_list",
    }


def test_tool_typo_skipped_when_no_close_match() -> None:
    engine = _engine_with_tools(["create_issue", "list_commits"])
    step = MagicMock()
    step.id = "fetch"
    step.step_type = StepType.TOOL
    step.tool = "totally_unrelated"
    step.mcp = "github"
    step.code = None

    err = create_error("TOOL_UNAVAILABLE", tool_name="totally_unrelated")
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    meta = engine._build_error_metadata(step=step, exc=err, rendered_params={}, context=ctx)
    assert "suggested_fix" not in meta


def test_no_prior_outputs_omits_step_inputs(engine: WorkflowEngine) -> None:
    step = MagicMock()
    step.id = "first"
    step.step_type = StepType.CODE
    step.code = "raise RuntimeError('x')"
    step.tool = None
    step.mcp = None

    ctx = ExecutionContext(
        execution_id="e",
        workflow=MagicMock(),
        inputs={},
        config={},
    )

    meta = engine._build_error_metadata(
        step=step,
        exc=RuntimeError("x"),
        rendered_params={},
        context=ctx,
    )
    assert "step_inputs" not in meta


# ── template-error enrichment ──


def _template_err(variable: str, expression: str | None = None) -> Exception:
    """Build a TEMPLATE_ERROR carrying the engine-side annotations."""
    err = create_error("TEMPLATE_ERROR", variable=variable)
    err._template_variable = variable
    err._template_expression = expression or variable
    return err


def test_template_error_emits_expression_and_available_steps() -> None:
    engine = _engine_with_tools([])
    step = MagicMock()
    step.id = "use"
    step.step_type = StepType.TOOL
    step.tool = "noop"
    step.mcp = None
    step.code = None

    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.step_outputs["fetch"] = {"items": [1]}
    ctx.step_outputs["parse"] = {"ok": True}

    err = _template_err("steps.fetc.output.items")
    err._template_param_path = "data"

    meta = engine._build_error_metadata(step=step, exc=err, rendered_params=None, context=ctx)

    assert meta["template_expression"] == "steps.fetc.output.items"
    assert meta["param_path"] == "data"
    assert meta["available_steps"] == ["fetch", "parse"]


def test_template_error_step_id_typo_yields_suggested_fix() -> None:
    engine = _engine_with_tools([])
    step = MagicMock()
    step.id = "use"
    step.step_type = StepType.TOOL
    step.tool = "noop"
    step.mcp = None
    step.code = None

    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.step_outputs["fetch"] = {"items": [1]}

    err = _template_err("steps.fetc.output.items")
    err._template_param_path = "headers.X-Items"

    meta = engine._build_error_metadata(step=step, exc=err, rendered_params=None, context=ctx)
    assert meta["suggested_fix"] == {
        "op": "set",
        "path": "steps.use.params.headers.X-Items",
        "value": "{{ steps.fetch.output.items }}",
    }


def test_template_error_unknown_namespace_no_fix() -> None:
    engine = _engine_with_tools([])
    step = MagicMock()
    step.id = "use"
    step.step_type = StepType.TOOL
    step.tool = "noop"
    step.mcp = None
    step.code = None

    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.step_outputs["fetch"] = {"x": 1}

    err = _template_err("inputs.missing")
    err._template_param_path = "x"

    meta = engine._build_error_metadata(step=step, exc=err, rendered_params=None, context=ctx)
    assert "suggested_fix" not in meta
    assert meta["template_expression"] == "inputs.missing"
    assert meta["param_path"] == "x"
    assert meta["available_steps"] == ["fetch"]


def test_annotate_template_error_finds_param_path_in_nested_dict() -> None:
    engine = _engine_with_tools([])
    err = _template_err("steps.fetc.output.items")
    params = {
        "url": "https://x",
        "headers": {"Authorization": "Bearer {{ steps.fetc.output.items | json }}"},
    }
    engine._annotate_template_error(err, params=params, step_id="use", step_outputs={})
    assert err._template_param_path == "headers.Authorization"


def test_annotate_template_error_finds_param_path_in_list() -> None:
    engine = _engine_with_tools([])
    err = _template_err("steps.fetc.output.items")
    params = {"items": ["{{ steps.fetc.output.items }}", "static"]}
    engine._annotate_template_error(err, params=params, step_id="use", step_outputs={})
    assert err._template_param_path == "items.[0]"


def test_template_engine_attaches_variable_to_aelerror() -> None:
    """End-to-end: TemplateEngine.render_string raises with annotations
    that the engine reads back in ``_enrich_template_error``.
    """
    from ploston_core.errors import AELError
    from ploston_core.template.engine import TemplateEngine
    from ploston_core.template.types import TemplateContext

    eng = TemplateEngine()
    ctx = TemplateContext(
        inputs={},
        steps={"fetch": {"output": {"items": []}}},
        config={},
        execution_id="e",
    )
    with pytest.raises(AELError) as exc_info:
        eng.render_string("{{ steps.fetc.output.items }}", ctx)
    assert exc_info.value.code == "TEMPLATE_ERROR"
    assert exc_info.value._template_variable == "steps.fetc.output.items"
    assert exc_info.value._template_expression == "steps.fetc.output.items"


# ── cascade-skip on failed/skipped dependency ──


def test_find_failed_dependency_returns_failed_prior(engine: WorkflowEngine) -> None:
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.add_step_result(
        StepResult(step_id="fetch", status=StepStatus.FAILED, error=RuntimeError("boom"))
    )
    step = MagicMock()
    step.depends_on = ["fetch"]

    found = engine._find_failed_dependency(step, ctx)
    assert found is not None
    assert found[0] == "fetch"
    assert found[1].status == StepStatus.FAILED


def test_find_failed_dependency_returns_skipped_prior(engine: WorkflowEngine) -> None:
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.add_step_result(
        StepResult(
            step_id="fetch",
            status=StepStatus.SKIPPED,
            skip_reason="when condition not met",
        )
    )
    step = MagicMock()
    step.depends_on = ["fetch"]
    found = engine._find_failed_dependency(step, ctx)
    assert found is not None and found[0] == "fetch"


def test_find_failed_dependency_no_match_returns_none(engine: WorkflowEngine) -> None:
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    ctx.add_step_result(
        StepResult(step_id="fetch", status=StepStatus.COMPLETED, output={"ok": True})
    )
    step = MagicMock()
    step.depends_on = ["fetch"]
    assert engine._find_failed_dependency(step, ctx) is None


def test_find_failed_dependency_handles_missing_depends_on(engine: WorkflowEngine) -> None:
    ctx = ExecutionContext(execution_id="e", workflow=MagicMock(), inputs={}, config={})
    step = MagicMock()
    step.depends_on = None
    assert engine._find_failed_dependency(step, ctx) is None
