"""Specification tests for ploston_core.plugins.types.

These tests assert the *intended* contract of the plugin context/result types,
not merely the current implementation. Where current behavior diverges from the
documented contract, the test is written against the contract and allowed to
fail (RED) so the integrator can act on it.
"""

from datetime import datetime

import pytest

from ploston_core.plugins.types import (
    HookResult,
    PluginDecision,
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

# ---------------------------------------------------------------------------
# PluginDecision — open-core contract
# ---------------------------------------------------------------------------


class TestPluginDecision:
    def test_oss_only_exposes_continue(self):
        """Per the open-core model, OSS plugins may only ever decide CONTINUE.

        SKIP/ABORT/RETRY are premium-only control-flow decisions and must NOT
        be available in the OSS enum surface. If they appear here, OSS plugins
        could alter control flow, violating the observe/transform-only contract.
        """
        members = {m.name for m in PluginDecision}
        assert members == {"CONTINUE"}, (
            "OSS PluginDecision must expose only CONTINUE; control-flow "
            f"decisions must be premium-only. Found: {members}"
        )

    def test_continue_value(self):
        assert PluginDecision.CONTINUE.value == "continue"


# ---------------------------------------------------------------------------
# HookResult
# ---------------------------------------------------------------------------


class TestHookResult:
    def test_defaults(self):
        r = HookResult(data="x")
        assert r.data == "x"
        assert r.decision is PluginDecision.CONTINUE
        assert r.modified is False
        assert r.metadata == {}

    def test_unchanged_factory(self):
        r = HookResult.unchanged("payload")
        assert r.data == "payload"
        assert r.modified is False
        assert r.decision is PluginDecision.CONTINUE
        assert r.metadata == {}

    def test_changed_factory_sets_modified(self):
        r = HookResult.changed("payload")
        assert r.data == "payload"
        assert r.modified is True
        assert r.decision is PluginDecision.CONTINUE

    def test_changed_factory_carries_metadata(self):
        r = HookResult.changed("payload", {"k": "v"})
        assert r.metadata == {"k": "v"}

    def test_changed_factory_metadata_defaults_to_empty_dict(self):
        r = HookResult.changed("payload")
        assert r.metadata == {}

    def test_metadata_is_independent_per_instance(self):
        """default_factory must not share a single dict across instances."""
        a = HookResult(data=1)
        b = HookResult(data=2)
        a.metadata["x"] = 1
        assert b.metadata == {}


# ---------------------------------------------------------------------------
# Context dataclasses — construction & invariants
# ---------------------------------------------------------------------------


class TestRequestContext:
    def test_required_and_default_fields(self):
        ctx = RequestContext(workflow_id="wf", inputs={"a": 1}, execution_id="exec-1")
        assert ctx.workflow_id == "wf"
        assert ctx.inputs == {"a": 1}
        assert ctx.execution_id == "exec-1"
        assert isinstance(ctx.timestamp, datetime)
        assert ctx.metadata == {}

    def test_metadata_independent(self):
        a = RequestContext(workflow_id="wf", inputs={}, execution_id="e")
        b = RequestContext(workflow_id="wf", inputs={}, execution_id="e")
        a.metadata["x"] = 1
        assert b.metadata == {}


class TestStepContext:
    def test_fields_and_defaults(self):
        ctx = StepContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="search",
            params={"q": "hi"},
        )
        assert ctx.step_index == 0
        assert ctx.total_steps == 0
        assert ctx.metadata == {}
        assert ctx.tool_name == "search"

    def test_tool_name_may_be_none_for_code_steps(self):
        ctx = StepContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="code",
            tool_name=None,
            params={},
        )
        assert ctx.tool_name is None


class TestStepResultContext:
    def test_success_path_defaults(self):
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output={"r": 1},
            success=True,
        )
        assert ctx.error is None
        assert ctx.duration_ms == 0
        assert ctx.metadata == {}

    def test_failure_path_carries_error(self):
        err = ValueError("boom")
        ctx = StepResultContext(
            workflow_id="wf",
            execution_id="e",
            step_id="s1",
            step_type="tool",
            tool_name="t",
            params={},
            output=None,
            success=False,
            error=err,
            duration_ms=12,
        )
        assert ctx.success is False
        assert ctx.error is err
        assert ctx.duration_ms == 12


class TestResponseContext:
    def test_fields_and_defaults(self):
        ctx = ResponseContext(
            workflow_id="wf",
            execution_id="e",
            inputs={"a": 1},
            outputs={"b": 2},
            success=True,
        )
        assert ctx.error is None
        assert ctx.duration_ms == 0
        assert ctx.step_count == 0
        assert ctx.metadata == {}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
