"""Static checks for ClickHouse schema files (S-295 / T-941).

These tests don't require a live ClickHouse — they validate that the SQL
files exist, contain the expected DDL surface, and play nicely with the
migration runner's `{{retention_days}}` substitution and statement splitter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ploston_core.telemetry.store.clickhouse.migrate import _split_statements

SCHEMA_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ploston_core"
    / "telemetry"
    / "store"
    / "clickhouse"
    / "schema"
)


def test_schema_directory_exists():
    assert SCHEMA_DIR.is_dir()


def test_expected_schema_files_present():
    names = sorted(p.name for p in SCHEMA_DIR.glob("*.sql"))
    assert names == [
        "001_executions.sql",
        "002_steps.sql",
        "003_tool_calls.sql",
        "010_views.sql",
    ]


def test_owned_tables_use_create_if_not_exists():
    """Idempotency: re-running migrations must be a no-op."""
    for fname in ("001_executions.sql", "002_steps.sql", "003_tool_calls.sql"):
        sql = (SCHEMA_DIR / fname).read_text()
        assert "CREATE TABLE IF NOT EXISTS" in sql, fname


def test_views_use_create_if_not_exists():
    sql = (SCHEMA_DIR / "010_views.sql").read_text()
    assert sql.count("CREATE VIEW IF NOT EXISTS") == 2


def test_executions_has_dec145_topology_columns():
    sql = (SCHEMA_DIR / "001_executions.sql").read_text()
    for col in ("session_id", "runner_id", "bridge_session_id", "tenant_id"):
        assert col in sql, col


def test_executions_has_aggregate_metric_columns():
    sql = (SCHEMA_DIR / "001_executions.sql").read_text()
    for col in ("step_count", "tool_call_count", "total_response_bytes"):
        assert col in sql, col


def test_tool_calls_has_response_size_columns():
    """S-294 fields land here via T-967 record extension."""
    sql = (SCHEMA_DIR / "003_tool_calls.sql").read_text()
    for col in ("params_bytes", "response_bytes", "error_code", "error_category"):
        assert col in sql, col


def test_tool_calls_has_call_id_bloom_filter():
    """Session Inspector Panel 4 drill-down depends on this."""
    sql = (SCHEMA_DIR / "003_tool_calls.sql").read_text()
    assert "INDEX idx_call_id call_id" in sql
    assert "TYPE bloom_filter" in sql


def test_tool_calls_has_session_and_runner_indexes():
    sql = (SCHEMA_DIR / "003_tool_calls.sql").read_text()
    assert "INDEX idx_session session_id" in sql
    assert "INDEX idx_runner  runner_id" in sql


def test_steps_partition_by_inserted_at():
    """started_at is Nullable on steps; PARTITION BY must use inserted_at."""
    sql = (SCHEMA_DIR / "002_steps.sql").read_text()
    assert "PARTITION BY toYYYYMM(inserted_at)" in sql


def test_owned_tables_use_plain_mergetree():
    """Engine choice locked by S-295: pure MergeTree + delete-then-insert."""
    for fname in ("001_executions.sql", "002_steps.sql", "003_tool_calls.sql"):
        sql = (SCHEMA_DIR / fname).read_text()
        assert "ENGINE = MergeTree()" in sql, fname
        assert "ReplacingMergeTree" not in sql, fname


def test_owned_tables_have_retention_template():
    """TTL must be templated, not hardcoded."""
    for fname in ("001_executions.sql", "002_steps.sql", "003_tool_calls.sql"):
        sql = (SCHEMA_DIR / fname).read_text()
        assert "{{retention_days}}" in sql, fname


def test_views_reference_otel_tables():
    sql = (SCHEMA_DIR / "010_views.sql").read_text()
    assert "FROM ploston.otel_logs" in sql
    assert "FROM ploston.otel_traces" in sql


def test_views_surface_session_id():
    """Session Inspector queries `ploston.events.session_id`."""
    sql = (SCHEMA_DIR / "010_views.sql").read_text()
    assert "LogAttributes['session_id']" in sql
    assert "AS session_id" in sql


def test_retention_substitution_clears_template():
    """Migration runner replaces `{{retention_days}}` before applying."""
    sql = (SCHEMA_DIR / "001_executions.sql").read_text()
    rendered = sql.replace("{{retention_days}}", "30")
    assert "{{" not in rendered
    assert "INTERVAL 30 DAY" in rendered


def test_ttl_wraps_datetime64_in_todatetime():
    """ClickHouse rejects DateTime64 in TTL expressions; toDateTime() is required."""
    for fname in ("001_executions.sql", "002_steps.sql", "003_tool_calls.sql"):
        sql = (SCHEMA_DIR / fname).read_text()
        # Find the TTL line and assert the toDateTime wrapper
        ttl_lines = [ln for ln in sql.splitlines() if ln.lstrip().startswith("TTL ")]
        assert ttl_lines, f"{fname} has no TTL clause"
        for line in ttl_lines:
            assert "toDateTime(" in line, f"{fname} TTL missing toDateTime wrap: {line}"


@pytest.mark.parametrize(
    "fname,expected_count",
    [
        ("001_executions.sql", 1),
        ("002_steps.sql", 1),
        ("003_tool_calls.sql", 1),
        ("010_views.sql", 2),
    ],
)
def test_statement_splitter_yields_expected_count(fname: str, expected_count: int):
    sql = (SCHEMA_DIR / fname).read_text().replace("{{retention_days}}", "7")
    statements = _split_statements(sql)
    assert len(statements) == expected_count, statements


def test_tool_calls_source_enum_values_documented():
    """Source widening: tool_step | code_block | direct (T-967)."""
    sql = (SCHEMA_DIR / "003_tool_calls.sql").read_text()
    assert "source" in sql

