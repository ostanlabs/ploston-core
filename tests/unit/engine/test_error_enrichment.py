"""S-292 P4d: tests for engine error metadata enrichment helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ploston_core.engine.error_enrichment import (
    build_code_context,
    build_skipped_metadata,
    extract_line_in_step,
    summarize_prior_outputs,
)
from ploston_core.engine.types import StepResult
from ploston_core.types import StepStatus

# ── build_code_context ──


def test_code_context_returns_window_around_error_line():
    code = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5"
    ctx = build_code_context(code, error_line=3, window=1)
    assert ctx == [
        {"line": 2, "text": "b = 2"},
        {"line": 3, "text": "c = 3", "is_error_line": True},
        {"line": 4, "text": "d = 4"},
    ]


def test_code_context_clamps_at_start_of_file():
    code = "a = 1\nb = 2\nc = 3"
    ctx = build_code_context(code, error_line=1, window=2)
    assert [c["line"] for c in ctx] == [1, 2, 3]
    assert ctx[0]["is_error_line"] is True


def test_code_context_clamps_at_end_of_file():
    code = "a = 1\nb = 2\nc = 3"
    ctx = build_code_context(code, error_line=3, window=5)
    assert [c["line"] for c in ctx] == [1, 2, 3]


# ── extract_line_in_step ──


def _raise_at_line_three() -> Exception:
    """Compile + run code under a ``<step:...>`` filename and return the exception."""
    src = "x = 1\ny = 2\nz = 1 / 0\n"
    try:
        exec(compile(src, "<step:foo>", "exec"))
    except Exception as e:
        return e
    raise AssertionError("expected exception")


def test_extract_line_finds_user_frame():
    exc = _raise_at_line_three()
    line, source = extract_line_in_step(exc, "x = 1\ny = 2\nz = 1 / 0\n")
    assert line == 3
    assert source == "z = 1 / 0"


def test_extract_line_returns_none_when_no_traceback():
    exc = ValueError("no tb")
    line, source = extract_line_in_step(exc, "a = 1")
    assert line is None and source is None


# ── summarize_prior_outputs ──


def _result(step_id: str, status: StepStatus, output: Any) -> StepResult:
    return StepResult(
        step_id=step_id,
        status=status,
        started_at=datetime.now(),
        completed_at=datetime.now(),
        duration_ms=10,
        output=output,
    )


def test_summarize_prior_outputs_dict_returns_keys():
    results = {"fetch": _result("fetch", StepStatus.COMPLETED, {"items": [], "total": 5})}
    summary = summarize_prior_outputs(results)
    assert summary == {"fetch": ["items", "total"]}


def test_summarize_prior_outputs_caps_long_dicts():
    huge = {f"k{i}": i for i in range(60)}
    results = {"big": _result("big", StepStatus.COMPLETED, huge)}
    summary = summarize_prior_outputs(results, max_keys=10)
    assert len(summary["big"]) == 11
    assert summary["big"][-1] == "...truncated"


def test_summarize_prior_outputs_handles_lists_and_scalars():
    results = {
        "list_step": _result("list_step", StepStatus.COMPLETED, [1, 2, 3]),
        "int_step": _result("int_step", StepStatus.COMPLETED, 42),
        "none_step": _result("none_step", StepStatus.COMPLETED, None),
    }
    summary = summarize_prior_outputs(results)
    assert summary["list_step"] == "<list, length=3>"
    assert summary["int_step"] == "<scalar:int>"
    assert summary["none_step"] == "<scalar:NoneType>"


def test_summarize_prior_outputs_skips_failed_and_skipped():
    results = {
        "ok": _result("ok", StepStatus.COMPLETED, {"a": 1}),
        "bad": _result("bad", StepStatus.FAILED, None),
        "skipped": _result("skipped", StepStatus.SKIPPED, None),
    }
    summary = summarize_prior_outputs(results)
    assert "ok" in summary
    assert "bad" not in summary
    assert "skipped" not in summary


# ── build_skipped_metadata ──


def test_skipped_metadata_includes_root_cause():
    err = RuntimeError("upstream broke")
    meta = build_skipped_metadata("upstream_step", err)
    assert meta == {
        "step_type": "skipped",
        "root_cause_step_id": "upstream_step",
        "root_cause_error": "upstream broke",
    }
