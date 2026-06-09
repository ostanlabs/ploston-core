"""Spec tests for config-driven fail_open on the real PluginDefinition.

BUG R-2 / DECISION D1: ``PluginRegistry.load_plugins`` contains an override
path::

    if hasattr(defn, "fail_open"):
        plugin.fail_open = getattr(defn, "fail_open", True)

but the production ``PluginDefinition`` dataclass carries no ``fail_open``
field, so the override is unreachable via real YAML/config. Operators cannot
configure a plugin as fail-closed.

These tests assert the *intended* contract using the REAL PluginDefinition
(not a synthetic subclass exposing the attribute):

  * ``PluginDefinition`` accepts a ``fail_open`` field, default True
    (today's behavior).
  * The registry propagates that field to the loaded plugin.
  * The plugin-chain error isolation honors the config-driven value:
      - fail_open=True  -> error isolated, chain continues.
      - fail_open=False -> error propagates, later plugins skipped.

RED today: the field is absent, so constructing
``PluginDefinition(..., fail_open=False)`` raises TypeError and the override
path can never fire.
"""

import dataclasses

import pytest

from ploston_core.config.models import PluginDefinition
from ploston_core.plugins.base import AELPlugin
from ploston_core.plugins.registry import PluginRegistry
from ploston_core.plugins.types import RequestContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_ctx():
    return RequestContext(workflow_id="wf", inputs={"a": 1}, execution_id="e")


class RaisingPlugin(AELPlugin):
    def on_request_received(self, context):
        raise RuntimeError("plugin exploded")


class RecordingPlugin(AELPlugin):
    """Appends its name to a shared sink when its hook runs."""

    def __init__(self, config=None):
        super().__init__(config)

    def on_request_received(self, context):
        self.config["sink"].append(self.name)
        return context


# ---------------------------------------------------------------------------
# Field presence / defaults
# ---------------------------------------------------------------------------


class TestPluginDefinitionFailOpenField:
    def test_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(PluginDefinition)}
        assert "fail_open" in field_names, (
            "PluginDefinition must expose a fail_open field so operators can "
            "configure fail-open/closed via config (BUG R-2)."
        )

    def test_default_is_true(self):
        """Default must match today's behavior: fail-open."""
        defn = PluginDefinition(name="logging", type="builtin")
        assert defn.fail_open is True

    def test_field_is_bool_typed(self):
        f = {f.name: f for f in dataclasses.fields(PluginDefinition)}["fail_open"]
        assert f.type in ("bool", bool)

    def test_can_construct_fail_closed(self):
        defn = PluginDefinition(name="logging", type="builtin", fail_open=False)
        assert defn.fail_open is False


# ---------------------------------------------------------------------------
# Registry propagation via real config
# ---------------------------------------------------------------------------


class TestRegistryHonorsConfigFailOpen:
    def test_default_definition_loads_fail_open_true(self):
        reg = PluginRegistry()
        reg.load_plugins([PluginDefinition(name="logging", type="builtin")])
        assert reg.plugins[0].fail_open is True

    def test_fail_closed_definition_propagates_to_plugin(self):
        reg = PluginRegistry()
        reg.load_plugins([PluginDefinition(name="logging", type="builtin", fail_open=False)])
        assert reg.plugins[0].fail_open is False


# ---------------------------------------------------------------------------
# End-to-end chain isolation honoring config-driven fail_open
# ---------------------------------------------------------------------------


class TestChainHonorsConfigFailOpen:
    def _registry_with(self, bad_fail_open: bool, sink: list):
        reg = PluginRegistry()
        bad = RaisingPlugin()
        bad.name = "bad"
        bad.priority = 10
        bad.fail_open = bad_fail_open

        after = RecordingPlugin({"sink": sink})
        after.name = "after"
        after.priority = 20

        # Inject directly (sorted by priority) to exercise chain semantics
        # independent of the loader.
        reg._plugins = [bad, after]
        return reg

    def test_fail_open_true_isolates_and_continues(self):
        sink: list = []
        reg = self._registry_with(bad_fail_open=True, sink=sink)
        result = reg.execute_request_received(_request_ctx())
        assert sink == ["after"], "fail_open=True must isolate error and continue"
        assert result is not None

    def test_fail_open_false_aborts_chain(self):
        sink: list = []
        reg = self._registry_with(bad_fail_open=False, sink=sink)
        with pytest.raises(RuntimeError, match="plugin exploded"):
            reg.execute_request_received(_request_ctx())
        assert sink == [], "fail_open=False must abort before later plugins run"

    def test_config_drives_fail_closed_end_to_end(self):
        """The whole point of BUG R-2: a fail_open=False config value must
        flow PluginDefinition -> registry -> plugin -> chain abort.

        Uses a file-based plugin definition so the config-driven override is
        exercised through the real loader, not direct attribute assignment.
        """
        reg = PluginRegistry()
        # Load a builtin then assert the config value took effect; combine
        # with a raising plugin to confirm abort semantics.
        reg.load_plugins([PluginDefinition(name="logging", type="builtin", fail_open=False)])
        loaded = reg.plugins[0]
        assert loaded.fail_open is False

        # Now stand up a chain where the fail-closed plugin raises.
        sink: list = []
        loaded.on_request_received = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
        after = RecordingPlugin({"sink": sink})
        after.name = "after"
        after.priority = 99
        reg._plugins = [loaded, after]

        with pytest.raises(RuntimeError, match="boom"):
            reg.execute_request_received(_request_ctx())
        assert sink == []
