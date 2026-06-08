"""Specification tests for the builtin MetricsPlugin.

Where OpenTelemetry SDK is available, a real in-memory meter provider is
installed so we can assert actual metric emissions (counters/histograms with
the correct attributes). We also assert the critical state-hygiene contract:
per-request timing entries in `_request_times` MUST NOT leak — including when
a workflow fails.

Tests assert intended behavior; defects are allowed to fail (RED).
"""

import pytest

from ploston_core.plugins.builtin.metrics import MetricsPlugin
from ploston_core.plugins.types import (
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

otel_sdk = pytest.importorskip("opentelemetry.sdk.metrics")
from opentelemetry import metrics as otel_metrics  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402


@pytest.fixture
def metric_reader(monkeypatch):
    """Install a fresh in-memory MeterProvider for the duration of a test.

    OTEL only allows setting the global meter provider once per process, so we
    monkeypatch the module-level provider used by metrics.get_meter to bypass
    the one-time-set guard.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(otel_metrics, "get_meter", lambda *a, **k: provider.get_meter("spec"))
    return reader


def _collect(reader):
    """Return {metric_name: [data_points]} from the in-memory reader."""
    data = reader.get_metrics_data()
    out = {}
    if not data:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out


def _request_ctx(execution_id="e"):
    return RequestContext(workflow_id="wf", inputs={}, execution_id=execution_id)


def _step_after(success=True, duration_ms=20):
    return StepResultContext(
        workflow_id="wf",
        execution_id="e",
        step_id="s1",
        step_type="tool",
        tool_name="t",
        params={},
        output={"r": 1},
        success=success,
        duration_ms=duration_ms,
    )


def _response(success=True, execution_id="e", duration_ms=100):
    return ResponseContext(
        workflow_id="wf",
        execution_id=execution_id,
        inputs={},
        outputs={"o": 1},
        success=success,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self):
        p = MetricsPlugin()
        assert p._prefix == "ploston"
        assert p._emit_histogram is True
        assert p._request_times == {}

    def test_priority_runs_late(self):
        assert MetricsPlugin.priority == 90

    def test_custom_prefix(self, metric_reader):
        p = MetricsPlugin({"prefix": "myco"})
        assert p._prefix == "myco"


# ---------------------------------------------------------------------------
# Emission correctness
# ---------------------------------------------------------------------------


class TestEmission:
    def test_request_counter_emitted(self, metric_reader):
        p = MetricsPlugin()
        ret = p.on_request_received(_request_ctx())
        assert ret.workflow_id == "wf"  # observe-only passthrough
        metrics = _collect(metric_reader)
        assert "ploston_requests_total" in metrics
        dp = metrics["ploston_requests_total"][0]
        assert dp.value == 1
        assert dp.attributes.get("workflow") == "wf"

    def test_step_counter_records_status(self, metric_reader):
        p = MetricsPlugin()
        p.on_step_after(_step_after(success=True))
        metrics = _collect(metric_reader)
        assert "ploston_steps_total" in metrics
        attrs = metrics["ploston_steps_total"][0].attributes
        assert attrs.get("status") == "success"
        assert attrs.get("step") == "s1"
        assert attrs.get("step_type") == "tool"

    def test_failed_step_status_is_error(self, metric_reader):
        p = MetricsPlugin()
        p.on_step_after(_step_after(success=False))
        metrics = _collect(metric_reader)
        attrs = metrics["ploston_steps_total"][0].attributes
        assert attrs.get("status") == "error"

    def test_step_duration_histogram_in_seconds(self, metric_reader):
        p = MetricsPlugin()
        p.on_step_after(_step_after(duration_ms=2500))
        metrics = _collect(metric_reader)
        assert "ploston_step_duration_seconds" in metrics
        dp = metrics["ploston_step_duration_seconds"][0]
        assert dp.sum == pytest.approx(2.5)

    def test_workflow_duration_histogram(self, metric_reader):
        p = MetricsPlugin()
        p.on_response_ready(_response(duration_ms=1000))
        metrics = _collect(metric_reader)
        assert "ploston_workflow_duration_seconds" in metrics
        dp = metrics["ploston_workflow_duration_seconds"][0]
        assert dp.sum == pytest.approx(1.0)

    def test_histogram_disabled_emits_no_duration(self, metric_reader):
        p = MetricsPlugin({"emit_histogram": False})
        p.on_step_after(_step_after())
        p.on_response_ready(_response())
        metrics = _collect(metric_reader)
        assert "ploston_step_duration_seconds" not in metrics
        assert "ploston_workflow_duration_seconds" not in metrics


# ---------------------------------------------------------------------------
# step_before records start time in metadata (observe/transform-only)
# ---------------------------------------------------------------------------


class TestStepBefore:
    def test_records_start_time_in_metadata(self, metric_reader):
        p = MetricsPlugin()
        ctx = StepContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
        )
        ret = p.on_step_before(ctx)
        assert ret is ctx
        assert "_metrics_start_time" in ctx.metadata


# ---------------------------------------------------------------------------
# State hygiene — no per-request leak (incl. on failure)
# ---------------------------------------------------------------------------


class TestRequestTimeCleanup:
    def test_successful_workflow_cleans_up_timing(self, metric_reader):
        p = MetricsPlugin()
        p.on_request_received(_request_ctx("abc"))
        assert "abc" in p._request_times
        p.on_response_ready(_response(execution_id="abc", success=True))
        assert "abc" not in p._request_times
        assert p._request_times == {}

    def test_failed_workflow_still_cleans_up_timing(self, metric_reader):
        """Prior audit flag: metrics may leak per-request entries on failure.

        Intended contract: the per-request timing entry must be removed even
        when the workflow FAILED, otherwise `_request_times` grows unbounded
        (a memory leak) under sustained failure load.
        """
        p = MetricsPlugin()
        p.on_request_received(_request_ctx("fail-1"))
        p.on_response_ready(_response(execution_id="fail-1", success=False))
        assert "fail-1" not in p._request_times, (
            "MetricsPlugin leaked a per-request timing entry after a failed "
            "workflow — _request_times must be cleaned up regardless of success."
        )

    def test_no_unbounded_growth_under_repeated_failures(self, metric_reader):
        p = MetricsPlugin()
        for i in range(50):
            eid = f"req-{i}"
            p.on_request_received(_request_ctx(eid))
            p.on_response_ready(_response(execution_id=eid, success=False))
        assert p._request_times == {}, (
            f"_request_times leaked {len(p._request_times)} entries under repeated failures."
        )


# ---------------------------------------------------------------------------
# Graceful degradation when OTEL is absent
# ---------------------------------------------------------------------------


class TestNoOtel:
    def test_init_handles_missing_opentelemetry(self, monkeypatch):
        """If the opentelemetry package is unavailable at init, the plugin must
        degrade gracefully: construct successfully with all metric instruments
        left as None (no-ops), never raising ImportError."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "opentelemetry" or name.startswith("opentelemetry"):
                raise ImportError("simulated: opentelemetry not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        p = MetricsPlugin()
        assert p._meter is None
        assert p._request_counter is None
        assert p._step_counter is None
        assert p._step_duration is None
        assert p._workflow_duration is None

    def test_hooks_are_noops_without_meter(self):
        """If no meter was initialized, hooks must still pass context through
        without error (observe-only, fail-safe)."""
        p = MetricsPlugin()
        p._request_counter = None
        p._step_counter = None
        p._step_duration = None
        p._workflow_duration = None

        ctx = _request_ctx("x")
        assert p.on_request_received(ctx) is ctx
        assert "x" in p._request_times  # timing still tracked
        sr = _step_after()
        assert p.on_step_after(sr) is sr
        resp = _response(execution_id="x")
        assert p.on_response_ready(resp) is resp
        assert "x" not in p._request_times  # cleanup still happens


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
