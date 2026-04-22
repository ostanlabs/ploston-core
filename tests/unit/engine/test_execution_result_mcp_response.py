"""S-271: ExecutionResult.to_mcp_response / to_dict telemetry enrichment."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ploston_core.engine.types import ExecutionResult, StepResult
from ploston_core.types import ExecutionStatus, StepStatus


def _build_result(steps=None, status=ExecutionStatus.COMPLETED, outputs=None, error=None):
    return ExecutionResult(
        execution_id="exec-1",
        workflow_id="wf",
        workflow_version="1.0.0",
        status=status,
        started_at=datetime(2026, 4, 22, 10, 0, 0),
        completed_at=datetime(2026, 4, 22, 10, 0, 5),
        duration_ms=5000,
        outputs=outputs if outputs is not None else {"items": [1, 2]},
        steps=steps or [],
        error=error,
    )


def _step(
    step_id="s1",
    status=StepStatus.COMPLETED,
    duration_ms=100,
    debug_log=None,
    error=None,
    skip_reason=None,
):
    return StepResult(
        step_id=step_id,
        status=status,
        duration_ms=duration_ms,
        debug_log=debug_log or [],
        error=error,
        skip_reason=skip_reason,
    )


# ── MR-01: execution object with duration_ms and steps ──


def test_mr01_response_includes_execution_object():
    r = _build_result(steps=[_step("s1")])
    resp = r.to_mcp_response()
    assert "execution" in resp
    assert resp["execution"]["duration_ms"] == 5000
    assert isinstance(resp["execution"]["steps"], dict)
    assert "s1" in resp["execution"]["steps"]


def test_mr02_step_entries_include_status_and_duration():
    r = _build_result(steps=[_step("fetch", duration_ms=1200)])
    resp = r.to_mcp_response()
    step_data = resp["execution"]["steps"]["fetch"]
    assert step_data["status"] == "completed"
    assert step_data["duration_ms"] == 1200


def test_mr03_step_debug_log_included_when_non_empty():
    r = _build_result(steps=[_step("fetch", debug_log=["msg1", "msg2"])])
    resp = r.to_mcp_response()
    assert resp["execution"]["steps"]["fetch"]["debug_log"] == ["msg1", "msg2"]


def test_mr04_step_debug_log_omitted_when_empty():
    r = _build_result(steps=[_step("fetch", debug_log=[])])
    resp = r.to_mcp_response()
    assert "debug_log" not in resp["execution"]["steps"]["fetch"]


def test_mr05_workflow_version_included():
    r = _build_result()
    resp = r.to_mcp_response()
    assert resp["workflow_version"] == "1.0.0"


def test_mr06_failed_step_includes_error_string():
    err = RuntimeError("boom")
    r = _build_result(steps=[_step("x", status=StepStatus.FAILED, error=err)])
    resp = r.to_mcp_response()
    assert resp["execution"]["steps"]["x"]["error"] == "boom"


def test_mr07_skipped_step_includes_skip_reason():
    r = _build_result(steps=[_step("x", status=StepStatus.SKIPPED, skip_reason="when=false")])
    resp = r.to_mcp_response()
    assert resp["execution"]["steps"]["x"]["skip_reason"] == "when=false"
    assert resp["execution"]["steps"]["x"]["status"] == "skipped"


# ── Rev 3 Issue A: top-level key is "result", not "outputs" ──


def test_top_level_key_is_result_not_outputs():
    r = _build_result(outputs={"report": {"count": 3}})
    resp = r.to_mcp_response()
    assert resp["result"] == {"report": {"count": 3}}
    assert "outputs" not in resp


def test_response_includes_execution_id_and_status():
    r = _build_result()
    resp = r.to_mcp_response()
    assert resp["execution_id"] == "exec-1"
    assert resp["status"] == "completed"


def test_failed_execution_response_includes_error():
    r = _build_result(
        status=ExecutionStatus.FAILED,
        outputs={},
        error=RuntimeError("workflow blew up"),
    )
    resp = r.to_mcp_response()
    assert resp["status"] == "failed"
    assert resp["error"] == "workflow blew up"


# ── MR-08: to_dict includes debug_log ──


def test_mr08_to_dict_includes_debug_log_when_populated():
    r = _build_result(steps=[_step("s1", debug_log=["x"])])
    d = r.to_dict()
    assert d["steps"][0]["debug_log"] == ["x"]


def test_mr08b_to_dict_debug_log_none_when_empty():
    r = _build_result(steps=[_step("s1", debug_log=[])])
    d = r.to_dict()
    assert d["steps"][0]["debug_log"] is None


# ── MR-09 integration: _handle_run returns to_mcp_response payload ──


@pytest.mark.asyncio
async def test_mr09_handle_run_surfaces_execution_telemetry():
    from ploston_core.workflow.tools import WorkflowToolsProvider

    step = _step("analyze", debug_log=["computed 3 items"])
    result = _build_result(steps=[step], outputs={"count": 3})

    engine = MagicMock()
    engine.execute = AsyncMock(return_value=result)

    provider = WorkflowToolsProvider(
        workflow_registry=MagicMock(),
        workflow_engine=engine,
    )
    resp = await provider._handle_run({"name": "my_wf", "inputs": {}})

    assert resp["result"] == {"count": 3}
    assert resp["status"] == "completed"
    assert resp["execution"]["steps"]["analyze"]["debug_log"] == ["computed 3 items"]
    assert resp["execution"]["duration_ms"] == 5000
    assert resp["workflow_version"] == "1.0.0"
