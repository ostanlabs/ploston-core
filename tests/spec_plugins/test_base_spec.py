"""Specification tests for ploston_core.plugins.base.AELPlugin.

Asserts the base-class contract: default hooks are pure pass-through observers
(observe/transform-only), construction handles config, and the class-level
defaults match the documented open-core invariants.
"""

import pytest

from ploston_core.plugins.base import AELPlugin
from ploston_core.plugins.types import (
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)


def _request_ctx():
    return RequestContext(workflow_id="wf", inputs={"a": 1}, execution_id="e")


def _step_ctx():
    return StepContext(
        workflow_id="wf",
        execution_id="e",
        step_id="s1",
        step_type="tool",
        tool_name="t",
        params={"p": 1},
    )


def _step_result_ctx():
    return StepResultContext(
        workflow_id="wf",
        execution_id="e",
        step_id="s1",
        step_type="tool",
        tool_name="t",
        params={},
        output={"r": 1},
        success=True,
    )


def _response_ctx():
    return ResponseContext(
        workflow_id="wf",
        execution_id="e",
        inputs={},
        outputs={"o": 1},
        success=True,
    )


class TestConstruction:
    def test_default_config_is_empty_dict(self):
        p = AELPlugin()
        assert p.config == {}

    def test_none_config_normalized_to_empty_dict(self):
        p = AELPlugin(None)
        assert p.config == {}

    def test_config_is_stored(self):
        cfg = {"level": "DEBUG"}
        p = AELPlugin(cfg)
        assert p.config == cfg

    def test_class_default_invariants(self):
        """Defaults documented in the contract."""
        assert AELPlugin.name == "base"
        assert AELPlugin.priority == 50
        # fail_open defaults True: a misbehaving plugin should not, by default,
        # be able to abort the whole execution.
        assert AELPlugin.fail_open is True


class TestDefaultHooksArePassThrough:
    """Base hooks must observe only — return the SAME context unchanged."""

    def test_on_request_received_returns_same_object(self):
        ctx = _request_ctx()
        assert AELPlugin().on_request_received(ctx) is ctx

    def test_on_step_before_returns_same_object(self):
        ctx = _step_ctx()
        assert AELPlugin().on_step_before(ctx) is ctx

    def test_on_step_after_returns_same_object(self):
        ctx = _step_result_ctx()
        assert AELPlugin().on_step_after(ctx) is ctx

    def test_on_response_ready_returns_same_object(self):
        ctx = _response_ctx()
        assert AELPlugin().on_response_ready(ctx) is ctx

    def test_default_hooks_do_not_mutate_context(self):
        ctx = _request_ctx()
        AELPlugin().on_request_received(ctx)
        assert ctx.inputs == {"a": 1}


class TestRepr:
    def test_repr_includes_name_and_priority(self):
        class MyPlugin(AELPlugin):
            name = "my-plugin"
            priority = 7

        r = repr(MyPlugin())
        assert "MyPlugin" in r
        assert "my-plugin" in r
        assert "7" in r


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
