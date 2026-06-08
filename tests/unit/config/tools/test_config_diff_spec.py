"""Specification tests for the config_diff tool handler.

Asserts the CORRECT diff semantics described by the handler docstring:
- added / removed / changed keys are reflected in the unified diff,
- staged_changes is a flattened path/value list of pending changes,
- total_changes counts the flattened pending changes,
- has_changes is True iff there is something staged,
- nested structures flatten to dot-paths,
- no-change case reports no changes.

These use a real StagedConfig (pure, no external services) driven through its
public set() API so the diff reflects genuine base-vs-staged deltas.
"""

import asyncio
from pathlib import Path

import pytest

from ploston_core.config.loader import ConfigLoader
from ploston_core.config.staged_config import StagedConfig
from ploston_core.config.tools.config_diff import (
    _flatten_changes,
    handle_config_diff,
)


def staged_from_base(tmp_path: Path, base_yaml: str | None) -> StagedConfig:
    """Build a StagedConfig whose base is loaded from base_yaml (or empty)."""
    loader = ConfigLoader()
    if base_yaml is not None:
        config_file = tmp_path / "ploston-config.yaml"
        config_file.write_text(base_yaml)
        loader.load(config_file)
    return StagedConfig(loader)


def run(coro):
    return asyncio.run(coro)


class TestFlattenChanges:
    """Pure unit tests for the _flatten_changes helper."""

    def test_empty_dict_yields_no_entries(self):
        assert _flatten_changes({}) == []

    def test_flat_keys(self):
        result = _flatten_changes({"a": 1, "b": "x"})
        assert {"path": "a", "value": 1} in result
        assert {"path": "b", "value": "x"} in result
        assert len(result) == 2

    def test_nested_keys_become_dot_paths(self):
        result = _flatten_changes({"server": {"port": 9000}})
        assert result == [{"path": "server.port", "value": 9000}]

    def test_deeply_nested(self):
        result = _flatten_changes({"mcp": {"servers": {"github": {"command": "npx"}}}})
        assert result == [{"path": "mcp.servers.github.command", "value": "npx"}]

    def test_multiple_leaves_under_one_branch(self):
        result = _flatten_changes({"s": {"a": 1, "b": 2}})
        paths = {e["path"]: e["value"] for e in result}
        assert paths == {"s.a": 1, "s.b": 2}

    def test_empty_nested_dict_yields_nothing(self):
        # An empty sub-dict has no leaves, so contributes no entries.
        assert _flatten_changes({"s": {}}) == []


class TestHandleConfigDiffNoChanges:
    def test_no_changes_reports_no_changes(self, tmp_path):
        staged = staged_from_base(tmp_path, "server:\n  port: 8022\n")
        result = run(handle_config_diff({}, staged))

        assert result["has_changes"] is False
        assert result["total_changes"] == 0
        assert result["staged_changes"] == []
        # Unified diff should be empty when base == merged.
        assert result["unified_diff"].strip() == ""

    def test_empty_base_no_changes(self, tmp_path):
        staged = staged_from_base(tmp_path, None)
        result = run(handle_config_diff({}, staged))
        assert result["has_changes"] is False
        assert result["total_changes"] == 0


class TestHandleConfigDiffAddedKeys:
    def test_added_key_is_a_change(self, tmp_path):
        staged = staged_from_base(tmp_path, "server:\n  port: 8022\n")
        staged.set("server.host", "localhost")

        result = run(handle_config_diff({}, staged))

        assert result["has_changes"] is True
        assert result["total_changes"] == 1
        assert {"path": "server.host", "value": "localhost"} in result["staged_changes"]
        # The newly added key should appear in the unified diff.
        assert "host" in result["unified_diff"]

    def test_brand_new_top_level_section(self, tmp_path):
        staged = staged_from_base(tmp_path, "server:\n  port: 8022\n")
        staged.set("logging.level", "DEBUG")

        result = run(handle_config_diff({}, staged))
        assert result["has_changes"] is True
        assert {"path": "logging.level", "value": "DEBUG"} in result["staged_changes"]


class TestHandleConfigDiffChangedKeys:
    def test_changed_value_is_a_change(self, tmp_path):
        staged = staged_from_base(tmp_path, "server:\n  port: 8022\n")
        staged.set("server.port", 9000)

        result = run(handle_config_diff({}, staged))

        assert result["has_changes"] is True
        assert {"path": "server.port", "value": 9000} in result["staged_changes"]
        # A changed value must show both old and new in the unified diff.
        diff = result["unified_diff"]
        assert "9000" in diff
        assert "8022" in diff

    def test_total_changes_counts_each_leaf(self, tmp_path):
        staged = staged_from_base(tmp_path, "server:\n  port: 8022\n")
        staged.set("server.port", 9000)
        staged.set("server.host", "0.0.0.0")
        staged.set("logging.level", "INFO")

        result = run(handle_config_diff({}, staged))
        # Three distinct leaf changes were staged.
        assert result["total_changes"] == 3
        paths = {e["path"] for e in result["staged_changes"]}
        assert paths == {"server.port", "server.host", "logging.level"}


class TestHandleConfigDiffNestedStructures:
    def test_nested_staged_changes_flatten_to_dot_paths(self, tmp_path):
        staged = staged_from_base(tmp_path, None)
        staged.set("mcp.servers.github.command", "npx")
        staged.set("mcp.servers.github.args", ["-y", "server-github"])

        result = run(handle_config_diff({}, staged))

        paths = {e["path"]: e["value"] for e in result["staged_changes"]}
        assert paths["mcp.servers.github.command"] == "npx"
        assert paths["mcp.servers.github.args"] == ["-y", "server-github"]
        assert result["total_changes"] == 2


class TestHandleConfigDiffContract:
    def test_result_has_required_keys(self, tmp_path):
        staged = staged_from_base(tmp_path, None)
        staged.set("server.port", 1234)
        result = run(handle_config_diff({}, staged))
        for key in ("has_changes", "total_changes", "unified_diff", "staged_changes"):
            assert key in result

    def test_handler_ignores_arguments(self, tmp_path):
        # The tool takes no arguments; extra args must not affect output.
        staged = staged_from_base(tmp_path, None)
        staged.set("server.port", 1234)
        a = run(handle_config_diff({}, staged))
        b = run(handle_config_diff({"unexpected": "value"}, staged))
        assert a == b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
