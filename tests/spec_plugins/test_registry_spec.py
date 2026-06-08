"""Specification tests for ploston_core.plugins.registry.PluginRegistry.

Covers loading (builtin/file/package), enable/disable, ordering, hook dispatch
across all four context types, HookResult normalization, the observe/transform
contract, and error isolation (fail_open semantics).

Tests assert the *intended* contract. Divergences are allowed to fail (RED).
"""

import textwrap

import pytest

from ploston_core.config.models import PluginDefinition
from ploston_core.plugins.base import AELPlugin
from ploston_core.plugins.registry import PluginLoadResult, PluginRegistry
from ploston_core.plugins.types import (
    HookResult,
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

# ---------------------------------------------------------------------------
# Helpers / test plugins
# ---------------------------------------------------------------------------


def _request_ctx(inputs=None):
    return RequestContext(workflow_id="wf", inputs=inputs or {"a": 1}, execution_id="e")


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


class RecordingPlugin(AELPlugin):
    """Records the order in which it was called via a shared list."""

    def __init__(self, config=None):
        super().__init__(config)
        self.calls = []

    def on_request_received(self, context):
        self.calls.append(("req", self.name))
        if self.config.get("order_sink") is not None:
            self.config["order_sink"].append(self.name)
        return context


class RaisingPlugin(AELPlugin):
    def on_request_received(self, context):
        raise RuntimeError("plugin exploded")

    def on_step_before(self, context):
        raise RuntimeError("plugin exploded")


class TransformInputsPlugin(AELPlugin):
    """Transforms request inputs and signals modification via HookResult."""

    def on_request_received(self, context):
        new_inputs = dict(context.inputs)
        new_inputs["injected"] = True
        context.inputs = new_inputs
        return HookResult.changed(context)


# ---------------------------------------------------------------------------
# PluginLoadResult
# ---------------------------------------------------------------------------


class TestPluginLoadResult:
    def test_counts_empty(self):
        r = PluginLoadResult()
        assert r.success_count == 0
        assert r.failure_count == 0

    def test_counts_populated(self):
        r = PluginLoadResult(loaded=[AELPlugin()], failed=[("x", "err")])
        assert r.success_count == 1
        assert r.failure_count == 1


# ---------------------------------------------------------------------------
# Loading: builtin
# ---------------------------------------------------------------------------


class TestLoadBuiltin:
    def test_load_builtin_logging(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="logging", type="builtin")])
        assert result.success_count == 1
        assert result.failure_count == 0
        assert reg.plugins[0].name == "logging"

    def test_unknown_builtin_is_recorded_as_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="does-not-exist", type="builtin")])
        assert result.success_count == 0
        assert result.failure_count == 1
        name, err = result.failed[0]
        assert name == "does-not-exist"
        assert "does-not-exist" in err or "Unknown builtin" in err

    def test_unknown_type_is_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="p", type="bogus")])
        assert result.failure_count == 1

    def test_config_overrides_applied(self):
        reg = PluginRegistry()
        reg.load_plugins([PluginDefinition(name="logging", type="builtin", priority=3)])
        assert reg.plugins[0].priority == 3
        assert reg.plugins[0].name == "logging"

    def test_fail_open_override_from_definition(self):
        """If a definition carries a fail_open attribute, the registry must
        apply it to the loaded plugin so operators can opt a plugin into
        fail-closed behavior.

        NOTE: the production PluginDefinition dataclass currently has NO
        fail_open field, so this override path is unreachable via real config.
        We pass a definition-like object exposing fail_open to exercise the
        documented override contract.
        """

        class DefnWithFailOpen(PluginDefinition):
            fail_open = False

        reg = PluginRegistry()
        defn = DefnWithFailOpen(name="logging", type="builtin")
        result = reg.load_plugins([defn])
        assert result.success_count == 1
        assert reg.plugins[0].fail_open is False


# ---------------------------------------------------------------------------
# Loading: disabled / ordering
# ---------------------------------------------------------------------------


class TestLoadEnableDisable:
    def test_disabled_plugin_skipped(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="logging", type="builtin", enabled=False)])
        assert result.success_count == 0
        assert result.failure_count == 0
        assert reg.plugins == []

    def test_mix_enabled_disabled(self):
        reg = PluginRegistry()
        result = reg.load_plugins(
            [
                PluginDefinition(name="logging", type="builtin", enabled=True),
                PluginDefinition(name="metrics", type="builtin", enabled=False),
            ]
        )
        assert result.success_count == 1
        assert {p.name for p in reg.plugins} == {"logging"}


class TestOrdering:
    def test_plugins_sorted_by_priority_ascending(self):
        reg = PluginRegistry()
        reg.load_plugins(
            [
                PluginDefinition(name="metrics", type="builtin", priority=90),
                PluginDefinition(name="logging", type="builtin", priority=10),
            ]
        )
        names = [p.name for p in reg.plugins]
        assert names == ["logging", "metrics"], (
            "Plugins must execute in ascending priority order (lower first)."
        )

    def test_plugins_property_returns_copy(self):
        reg = PluginRegistry()
        reg.load_plugins([PluginDefinition(name="logging", type="builtin")])
        snapshot = reg.plugins
        snapshot.clear()
        assert len(reg.plugins) == 1, "plugins property must return a defensive copy"

    def test_hook_dispatch_respects_priority_order(self):
        order = []
        p_late = RecordingPlugin({"order_sink": order})
        p_late.name = "late"
        p_late.priority = 90
        p_early = RecordingPlugin({"order_sink": order})
        p_early.name = "early"
        p_early.priority = 10

        reg = PluginRegistry()
        # Inject directly in scrambled order, then exercise the documented
        # contract that dispatch follows priority. We simulate load ordering:
        reg._plugins = sorted([p_late, p_early], key=lambda p: p.priority)

        reg.execute_request_received(_request_ctx())
        assert order == ["early", "late"]


# ---------------------------------------------------------------------------
# Loading: file
# ---------------------------------------------------------------------------


class TestLoadFromFile:
    def test_load_valid_plugin_file(self, tmp_path):
        plugin_src = textwrap.dedent(
            """
            from ploston_core.plugins.base import AELPlugin

            class MyFilePlugin(AELPlugin):
                name = "from-file"
            """
        )
        f = tmp_path / "myplugin.py"
        f.write_text(plugin_src)

        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="from-file", type="file", path=str(f))])
        assert result.success_count == 1
        assert reg.plugins[0].name == "from-file"

    def test_missing_path_is_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="p", type="file", path=None)])
        assert result.failure_count == 1

    def test_nonexistent_file_is_failure(self, tmp_path):
        reg = PluginRegistry()
        result = reg.load_plugins(
            [PluginDefinition(name="p", type="file", path=str(tmp_path / "nope.py"))]
        )
        assert result.failure_count == 1
        assert "not found" in result.failed[0][1].lower()

    def test_file_without_plugin_class_is_failure(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n")
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="p", type="file", path=str(f))])
        assert result.failure_count == 1


# ---------------------------------------------------------------------------
# Loading: package
# ---------------------------------------------------------------------------


class TestLoadFromPackage:
    def test_load_from_builtin_package(self):
        """An installed module exporting an AELPlugin subclass loads."""
        reg = PluginRegistry()
        result = reg.load_plugins(
            [
                PluginDefinition(
                    name="pkg",
                    type="package",
                    package="ploston_core.plugins.builtin.logging",
                )
            ]
        )
        assert result.success_count == 1

    def test_missing_package_name_is_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="p", type="package", package=None)])
        assert result.failure_count == 1

    def test_unimportable_package_is_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins(
            [PluginDefinition(name="p", type="package", package="totally_not_a_real_pkg_xyz")]
        )
        assert result.failure_count == 1

    def test_package_without_plugin_class_is_failure(self):
        reg = PluginRegistry()
        result = reg.load_plugins([PluginDefinition(name="p", type="package", package="json")])
        assert result.failure_count == 1


# ---------------------------------------------------------------------------
# Hook dispatch — all four chains, empty registry
# ---------------------------------------------------------------------------


class TestHookDispatchEmpty:
    def test_request_passthrough_empty_registry(self):
        reg = PluginRegistry()
        ctx = _request_ctx()
        res = reg.execute_request_received(ctx)
        assert isinstance(res, HookResult)
        assert res.data is ctx
        assert res.modified is False

    def test_step_before_empty(self):
        reg = PluginRegistry()
        ctx = _step_ctx()
        assert reg.execute_step_before(ctx).data is ctx

    def test_step_after_empty(self):
        reg = PluginRegistry()
        ctx = _step_result_ctx()
        assert reg.execute_step_after(ctx).data is ctx

    def test_response_ready_empty(self):
        reg = PluginRegistry()
        ctx = _response_ctx()
        assert reg.execute_response_ready(ctx).data is ctx


# ---------------------------------------------------------------------------
# Observe / transform contract
# ---------------------------------------------------------------------------


class TestObserveTransformContract:
    def test_raw_context_return_is_passed_through(self):
        """A plugin returning a raw context (not HookResult) must work and the
        chain reports not-modified when no HookResult signals modification."""

        class RawObserver(AELPlugin):
            def on_request_received(self, context):
                return context

        reg = PluginRegistry()
        reg._plugins = [RawObserver()]
        ctx = _request_ctx()
        res = reg.execute_request_received(ctx)
        assert res.data is ctx
        assert res.modified is False

    def test_transform_propagates_and_sets_modified(self):
        reg = PluginRegistry()
        reg._plugins = [TransformInputsPlugin()]
        ctx = _request_ctx({"a": 1})
        res = reg.execute_request_received(ctx)
        assert res.modified is True
        assert res.data.inputs["injected"] is True
        assert res.data.inputs["a"] == 1

    def test_transform_chains_across_plugins(self):
        class AddB(AELPlugin):
            def on_request_received(self, context):
                context.inputs = {**context.inputs, "b": 2}
                return HookResult.changed(context)

        reg = PluginRegistry()
        reg._plugins = [TransformInputsPlugin(), AddB()]
        res = reg.execute_request_received(_request_ctx({"a": 1}))
        assert res.data.inputs == {"a": 1, "injected": True, "b": 2}
        assert res.modified is True

    def test_oss_plugin_cannot_alter_control_flow(self):
        """The observe/transform-only contract: an OSS plugin has no mechanism
        to skip/abort the chain. Even if a plugin returns a HookResult whose
        decision is CONTINUE (the only OSS decision), all subsequent plugins
        still run and the chain completes normally.
        """
        ran = []

        class First(AELPlugin):
            def on_request_received(self, context):
                ran.append("first")
                return HookResult(data=context)  # decision defaults to CONTINUE

        class Second(AELPlugin):
            def on_request_received(self, context):
                ran.append("second")
                return context

        reg = PluginRegistry()
        reg._plugins = [First(), Second()]
        res = reg.execute_request_received(_request_ctx())
        assert ran == ["first", "second"], (
            "OSS plugins must not be able to short-circuit the hook chain."
        )
        assert isinstance(res, HookResult)


# ---------------------------------------------------------------------------
# Error isolation — fail_open semantics
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_fail_open_true_isolates_error_and_continues(self):
        """fail_open=True: a raising plugin must NOT break the chain; later
        plugins still run and the original context survives."""
        ran = []

        bad = RaisingPlugin()
        bad.name = "bad"
        bad.fail_open = True

        class After(AELPlugin):
            name = "after"

            def on_request_received(self, context):
                ran.append("after")
                return context

        reg = PluginRegistry()
        reg._plugins = [bad, After()]
        ctx = _request_ctx()
        res = reg.execute_request_received(ctx)
        assert ran == ["after"], "fail_open plugin error must not abort the chain"
        assert res.data is ctx

    def test_fail_open_false_propagates_error(self):
        """fail_open=False: the plugin opts into being able to abort, so the
        error must propagate out of the chain."""
        bad = RaisingPlugin()
        bad.name = "bad"
        bad.fail_open = False

        reg = PluginRegistry()
        reg._plugins = [bad]
        with pytest.raises(RuntimeError, match="plugin exploded"):
            reg.execute_request_received(_request_ctx())

    def test_fail_open_false_aborts_before_later_plugins(self):
        ran = []
        bad = RaisingPlugin()
        bad.name = "bad"
        bad.fail_open = False

        class After(AELPlugin):
            def on_request_received(self, context):
                ran.append("after")
                return context

        reg = PluginRegistry()
        reg._plugins = [bad, After()]
        with pytest.raises(RuntimeError):
            reg.execute_request_received(_request_ctx())
        assert ran == [], "later plugins must not run after a fail-closed abort"

    def test_error_isolation_applies_to_step_before(self):
        bad = RaisingPlugin()
        bad.name = "bad"
        bad.fail_open = True
        ran = []

        class After(AELPlugin):
            def on_step_before(self, context):
                ran.append("after")
                return context

        reg = PluginRegistry()
        reg._plugins = [bad, After()]
        reg.execute_step_before(_step_ctx())
        assert ran == ["after"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
