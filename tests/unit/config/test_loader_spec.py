"""Spec tests for ConfigLoader and env-var resolution.

Asserts intended behaviour from the docstrings:
- ``resolve_env_vars`` grammar: ``${VAR}``, ``${VAR:-default}``,
  ``${VAR:+alt}``, ``${VAR:?error}``, ``$$`` escape, name validation.
- ``deep_merge`` recursive merge semantics.
- ``ConfigLoader`` search order, file-not-found / empty-file / bad-YAML
  handling, validation failures, reload + change callbacks, and the
  used_defaults / has_config_file flags.

External boundary = filesystem (real temp files) + os.environ (monkeypatched).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ploston_core.config.loader import (
    ConfigLoader,
    deep_merge,
    get_config_loader,
    load_config,
    resolve_env_vars,
)
from ploston_core.errors import AELError

# ---------------------------------------------------------------------------
# resolve_env_vars
# ---------------------------------------------------------------------------


class TestResolveEnvVars:
    def test_required_var_set(self, monkeypatch) -> None:
        monkeypatch.setenv("MY_VAR", "value1")
        assert resolve_env_vars("x=${MY_VAR}") == "x=value1"

    def test_required_var_unset_raises(self, monkeypatch) -> None:
        monkeypatch.delenv("UNSET_VAR", raising=False)
        with pytest.raises(AELError) as ei:
            resolve_env_vars("${UNSET_VAR}")
        assert ei.value.code == "CONFIG_INVALID"

    def test_default_used_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("MAYBE", raising=False)
        assert resolve_env_vars("${MAYBE:-fallback}") == "fallback"

    def test_default_not_used_when_set(self, monkeypatch) -> None:
        monkeypatch.setenv("MAYBE", "real")
        assert resolve_env_vars("${MAYBE:-fallback}") == "real"

    def test_default_used_when_var_empty(self, monkeypatch) -> None:
        # POSIX (D2): ``${VAR:-default}`` treats empty == unset, so the default
        # is used when VAR is set but empty (colon-variant semantics). This is
        # consistent with ``${VAR:+alt}`` which uses an is-set-AND-nonempty check.
        monkeypatch.setenv("EMPTY", "")
        assert resolve_env_vars("${EMPTY:-fb}") == "fb"

    def test_alternate_when_set_nonempty(self, monkeypatch) -> None:
        monkeypatch.setenv("FLAG", "1")
        assert resolve_env_vars("${FLAG:+yes}") == "yes"

    def test_alternate_empty_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("FLAG", raising=False)
        assert resolve_env_vars("${FLAG:+yes}") == ""

    def test_alternate_empty_when_set_but_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("FLAG", "")
        assert resolve_env_vars("${FLAG:+yes}") == ""

    def test_required_with_custom_error(self, monkeypatch) -> None:
        monkeypatch.delenv("NEED", raising=False)
        with pytest.raises(AELError) as ei:
            resolve_env_vars("${NEED:?must set NEED}")
        assert ei.value.code == "CONFIG_INVALID"
        assert "must set NEED" in (ei.value.detail or "")

    def test_required_with_empty_error_message_uses_default(self, monkeypatch) -> None:
        monkeypatch.delenv("NEED", raising=False)
        with pytest.raises(AELError) as ei:
            resolve_env_vars("${NEED:?}")
        assert "NEED" in (ei.value.detail or "")

    def test_required_custom_error_raises_when_var_empty(self, monkeypatch) -> None:
        # POSIX (D2): ``${VAR:?msg}`` treats empty == unset, so an empty VAR
        # must raise the error rather than resolving to "".
        monkeypatch.setenv("NEED", "")
        with pytest.raises(AELError) as ei:
            resolve_env_vars("${NEED:?must set NEED}")
        assert ei.value.code == "CONFIG_INVALID"
        assert "must set NEED" in (ei.value.detail or "")

    def test_required_custom_error_passes_when_var_set_nonempty(self, monkeypatch) -> None:
        # ``${VAR:?msg}`` with a non-empty value resolves to that value.
        monkeypatch.setenv("NEED", "present")
        assert resolve_env_vars("${NEED:?must set NEED}") == "present"

    def test_dollar_escape(self) -> None:
        assert resolve_env_vars("price is $$5") == "price is $5"

    def test_dollar_escape_before_brace_is_literal(self, monkeypatch) -> None:
        # $${VAR} -> literal ${VAR}, no resolution.
        monkeypatch.setenv("VAR", "should-not-appear")
        assert resolve_env_vars("$${VAR}") == "${VAR}"

    def test_whitespace_trimmed_in_name(self, monkeypatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        assert resolve_env_vars("${ FOO }") == "bar"

    def test_invalid_name_left_untouched(self) -> None:
        # Names not matching [A-Za-z_][A-Za-z0-9_]* pass through unchanged.
        assert resolve_env_vars("${1BAD}") == "${1BAD}"

    def test_multiple_vars_in_one_string(self, monkeypatch) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert resolve_env_vars("${A}-${B}") == "1-2"


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat_override(self) -> None:
        assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}

    def test_nested_merge(self) -> None:
        base = {"server": {"host": "h", "port": 1}}
        override = {"server": {"port": 2}}
        assert deep_merge(base, override) == {"server": {"host": "h", "port": 2}}

    def test_override_replaces_non_dict(self) -> None:
        # dict over scalar -> replaced wholesale (not merged).
        assert deep_merge({"a": 1}, {"a": {"x": 1}}) == {"a": {"x": 1}}

    def test_scalar_over_dict_replaces(self) -> None:
        assert deep_merge({"a": {"x": 1}}, {"a": 5}) == {"a": 5}

    def test_base_not_mutated(self) -> None:
        base = {"a": {"x": 1}}
        deep_merge(base, {"a": {"y": 2}})
        assert base == {"a": {"x": 1}}

    def test_new_keys_added(self) -> None:
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# ConfigLoader.load — file resolution / errors / defaults
# ---------------------------------------------------------------------------


class TestConfigLoaderLoad:
    def test_explicit_path_missing_with_use_defaults(self, tmp_path: Path) -> None:
        loader = ConfigLoader()
        cfg = loader.load(tmp_path / "nope.yaml", use_defaults=True)
        assert cfg is not None
        assert loader.used_defaults is True
        assert loader.has_config_file is False

    def test_explicit_path_missing_without_use_defaults_raises(self, tmp_path: Path) -> None:
        loader = ConfigLoader()
        with pytest.raises(AELError) as ei:
            loader.load(tmp_path / "nope.yaml", use_defaults=False)
        assert ei.value.code == "CONFIG_INVALID"
        assert "not found" in (ei.value.detail or "").lower()

    def test_valid_file_loaded(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 9000\n")
        loader = ConfigLoader()
        cfg = loader.load(p)
        assert loader.used_defaults is False
        assert loader.has_config_file is True
        assert cfg.server.port == 9000

    def test_empty_file_uses_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        loader = ConfigLoader()
        loader.load(p)
        assert loader.used_defaults is True

    def test_file_with_only_comments_uses_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "comments.yaml"
        p.write_text("# just a comment\n")
        loader = ConfigLoader()
        loader.load(p)
        assert loader.used_defaults is True

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("key: [unclosed\n")
        loader = ConfigLoader()
        with pytest.raises(AELError) as ei:
            loader.load(p)
        assert ei.value.code == "CONFIG_INVALID"
        assert "yaml" in (ei.value.detail or "").lower()

    def test_validation_failure_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: not-an-int\n")
        loader = ConfigLoader()
        with pytest.raises(AELError) as ei:
            loader.load(p)
        assert ei.value.code == "CONFIG_INVALID"
        assert "validation failed" in (ei.value.detail or "").lower()

    def test_env_vars_resolved_in_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CFG_PORT_HOST", "example.com")
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  host: ${CFG_PORT_HOST}\n")
        loader = ConfigLoader()
        cfg = loader.load(p)
        assert cfg.server.host == "example.com"


class TestConfigPathResolution:
    def test_env_var_ploston_takes_precedence(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "from-env.yaml"
        target.write_text("server:\n  port: 1234\n")
        monkeypatch.setenv("PLOSTON_CONFIG_PATH", str(target))
        monkeypatch.delenv("AEL_CONFIG_PATH", raising=False)
        loader = ConfigLoader()
        cfg = loader.load()
        assert cfg.server.port == 1234

    def test_legacy_env_var_fallback(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "legacy.yaml"
        target.write_text("server:\n  port: 4321\n")
        monkeypatch.delenv("PLOSTON_CONFIG_PATH", raising=False)
        monkeypatch.setenv("AEL_CONFIG_PATH", str(target))
        loader = ConfigLoader()
        cfg = loader.load()
        assert cfg.server.port == 4321

    def test_local_file_resolution(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("PLOSTON_CONFIG_PATH", raising=False)
        monkeypatch.delenv("AEL_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ploston-config.yaml").write_text("server:\n  port: 5555\n")
        loader = ConfigLoader()
        cfg = loader.load()
        assert cfg.server.port == 5555

    def test_legacy_local_file_resolution(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("PLOSTON_CONFIG_PATH", raising=False)
        monkeypatch.delenv("AEL_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ael-config.yaml").write_text("server:\n  port: 6666\n")
        loader = ConfigLoader()
        cfg = loader.load()
        assert cfg.server.port == 6666

    def test_no_file_anywhere_falls_back_to_defaults(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("PLOSTON_CONFIG_PATH", raising=False)
        monkeypatch.delenv("AEL_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        # Point HOME at an empty dir so ~/.ploston/config.yaml does not exist.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        loader = ConfigLoader()
        loader.load()
        assert loader.used_defaults is True


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_unknown_key_is_warning_not_error(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"unknown_section": {}})
        assert result.valid is True
        assert any("Unknown configuration key" in w.message for w in result.warnings)

    def test_server_not_dict_is_error(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"server": "oops"})
        assert result.valid is False
        assert any(i.path == "server" for i in result.errors)

    def test_port_not_int_is_error(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"server": {"port": "x"}})
        assert result.valid is False
        assert any(i.path == "server.port" for i in result.errors)

    def test_nonpositive_timeout_is_error(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"execution": {"default_timeout": 0}})
        assert result.valid is False
        assert any(i.path == "execution.default_timeout" for i in result.errors)

    def test_negative_step_timeout_is_error(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"execution": {"step_timeout": -5}})
        assert result.valid is False

    def test_positive_timeout_ok(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"execution": {"default_timeout": 30}})
        assert result.valid is True

    def test_reserved_mcp_server_name_system_rejected(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"tools": {"mcp_servers": {"system": {"command": "x"}}}})
        assert result.valid is False
        assert any(i.path == "tools.mcp_servers.system" for i in result.errors)

    def test_non_reserved_mcp_server_name_ok(self) -> None:
        loader = ConfigLoader()
        result = loader.validate({"tools": {"mcp_servers": {"myserver": {"command": "x"}}}})
        assert result.valid is True


# ---------------------------------------------------------------------------
# get() / reload() / on_change() callbacks
# ---------------------------------------------------------------------------


class TestGetReloadCallbacks:
    def test_get_before_load_raises(self) -> None:
        loader = ConfigLoader()
        with pytest.raises(AELError) as ei:
            loader.get()
        assert ei.value.code == "CONFIG_INVALID"

    def test_get_after_load_returns_config(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 8000\n")
        loader = ConfigLoader()
        loaded = loader.load(p)
        assert loader.get() is loaded

    def test_reload_without_path_raises(self) -> None:
        loader = ConfigLoader()
        with pytest.raises(AELError) as ei:
            loader.reload()
        assert ei.value.code == "CONFIG_INVALID"

    def test_reload_picks_up_changes(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 1000\n")
        loader = ConfigLoader()
        loader.load(p)
        p.write_text("server:\n  port: 2000\n")
        reloaded = loader.reload()
        assert reloaded.server.port == 2000

    def test_on_change_callback_invoked_on_reload(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 1000\n")
        loader = ConfigLoader()
        loader.load(p)
        seen: list[int] = []
        loader.on_change(lambda cfg: seen.append(cfg.server.port))
        p.write_text("server:\n  port: 3000\n")
        loader.reload()
        assert seen == [3000]

    def test_reload_swallows_callback_exception(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 1000\n")
        loader = ConfigLoader()
        loader.load(p)

        def boom(cfg) -> None:
            raise RuntimeError("callback failed")

        loader.on_change(boom)
        p.write_text("server:\n  port: 4000\n")
        # Must not propagate the callback exception.
        result = loader.reload()
        assert result.server.port == 4000


# ---------------------------------------------------------------------------
# load_defaults / load_from_dict / watching placeholders / singletons
# ---------------------------------------------------------------------------


class TestMisc:
    def test_load_defaults_returns_config(self) -> None:
        loader = ConfigLoader()
        cfg = loader.load_defaults()
        assert cfg is not None
        assert cfg.server is not None

    def test_load_from_dict_invalid_raises(self) -> None:
        loader = ConfigLoader()
        with pytest.raises(AELError):
            loader.load_from_dict({"server": {"port": "bad"}})

    def test_start_stop_watching_are_noops(self) -> None:
        loader = ConfigLoader()
        # Placeholders: must not raise.
        loader.start_watching()
        loader.stop_watching()

    def test_get_config_loader_is_singleton(self) -> None:
        a = get_config_loader()
        b = get_config_loader()
        assert a is b

    def test_load_config_convenience(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text("server:\n  port: 7000\n")
        cfg = load_config(p)
        assert cfg.server.port == 7000
