"""S-303 T-976: telemetry-backend selection wiring in application.py.

Covers the three cases from the spec's §4.1:
  1. Default sqlite preserved when PLOSTON_TELEMETRY_BACKEND is unset.
  2. ClickHouse selected via env vars; the resulting config carries the
     discrete connection fields exactly as the env supplied them.
  3. Missing PLOSTON_CLICKHOUSE_HOST raises ValueError via __post_init__
     so the CP fails fast instead of silently falling back to sqlite.

The factory and store classes themselves are covered by S-295/S-296
tests; this file is strictly about the env → TelemetryStoreConfig bridge.
"""

from __future__ import annotations

import pytest

from ploston_core.application import _build_telemetry_store_config_from_env


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every PLOSTON_TELEMETRY_* and PLOSTON_CLICKHOUSE_* var so each
    test starts from a clean slate regardless of the host environment."""
    for key in (
        "PLOSTON_TELEMETRY_BACKEND",
        "TELEMETRY_STORE_SQLITE_PATH",
        "PLOSTON_CLICKHOUSE_HOST",
        "PLOSTON_CLICKHOUSE_PORT",
        "PLOSTON_CLICKHOUSE_DATABASE",
        "PLOSTON_CLICKHOUSE_USERNAME",
        "PLOSTON_CLICKHOUSE_PASSWORD",
        "PLOSTON_CLICKHOUSE_SECURE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_default_backend_is_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "sqlite"
    assert cfg.sqlite_path == "./data/telemetry.db"
    assert cfg.enabled is True


def test_sqlite_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEMETRY_STORE_SQLITE_PATH", "/tmp/custom.db")
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "sqlite"
    assert cfg.sqlite_path == "/tmp/custom.db"


def test_clickhouse_backend_full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PLOSTON_TELEMETRY_BACKEND", "clickhouse")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_HOST", "ch.example.com")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_PORT", "9001")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_DATABASE", "telemetry_v2")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_USERNAME", "writer")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_PASSWORD", "s3cret")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_SECURE", "true")
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "clickhouse"
    assert cfg.clickhouse_host == "ch.example.com"
    assert cfg.clickhouse_port == 9001
    assert cfg.clickhouse_database == "telemetry_v2"
    assert cfg.clickhouse_username == "writer"
    assert cfg.clickhouse_password == "s3cret"
    assert cfg.clickhouse_secure is True


def test_clickhouse_backend_minimal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only HOST is required; the other fields fall back to discrete defaults."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("PLOSTON_TELEMETRY_BACKEND", "clickhouse")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_HOST", "ch.local")
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "clickhouse"
    assert cfg.clickhouse_host == "ch.local"
    assert cfg.clickhouse_port == 8123
    assert cfg.clickhouse_database == "ploston"
    assert cfg.clickhouse_username == "default"
    assert cfg.clickhouse_password == ""
    assert cfg.clickhouse_secure is False


def test_clickhouse_missing_host_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per §4.1 case 3: don't silently fall back to sqlite — surface the error."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("PLOSTON_TELEMETRY_BACKEND", "clickhouse")
    with pytest.raises(ValueError, match="clickhouse_host"):
        _build_telemetry_store_config_from_env()


def test_backend_value_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PLOSTON_TELEMETRY_BACKEND", "ClickHouse")
    monkeypatch.setenv("PLOSTON_CLICKHOUSE_HOST", "ch.local")
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "clickhouse"


def test_unknown_backend_falls_through_to_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo or unknown backend value should not break startup; sqlite wins.

    The split between this safe fallback and the strict failure for
    misconfigured ClickHouse is intentional: unknown means "user didn't ask
    for ClickHouse", missing host means "user explicitly opted in but
    forgot to wire it".
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("PLOSTON_TELEMETRY_BACKEND", "memory")
    cfg = _build_telemetry_store_config_from_env()
    assert cfg.storage_type == "sqlite"
