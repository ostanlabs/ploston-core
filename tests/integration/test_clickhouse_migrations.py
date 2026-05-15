"""Integration tests for the ClickHouse migration runner (S-295 / T-941).

Spins up a real ClickHouse via testcontainers, applies migrations, and asserts
the schema produced by `run_migrations` matches the spec.

Skipped automatically when Docker / testcontainers are unavailable. Marked
`@pytest.mark.integration` so unit-only test runs don't pull in Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

clickhouse_connect = pytest.importorskip("clickhouse_connect")
testcontainers_clickhouse = pytest.importorskip("testcontainers.clickhouse")

from ploston_core.telemetry.store.clickhouse.migrate import run_migrations  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def clickhouse_container() -> AsyncIterator[tuple[str, int, str, str]]:
    """Yields (host, http_port, username, password) for a fresh ClickHouse."""
    container = testcontainers_clickhouse.ClickHouseContainer(
        image="clickhouse/clickhouse-server:24.8-alpine",
        username="test",
        password="test",
        dbname="default",
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        http_port = int(container.get_exposed_port(8123))
        yield host, http_port, "test", "test"
    finally:
        container.stop()


async def _client(host: str, port: int, username: str, password: str):
    return await clickhouse_connect.get_async_client(
        host=host,
        port=port,
        database="ploston",
        username=username,
        password=password,
    )


async def _migrate(host: str, port: int, username: str, password: str, retention_days: int = 7):
    await run_migrations(
        host=host,
        port=port,
        retention_days=retention_days,
        username=username,
        password=password,
    )


@pytest.fixture(scope="module")
async def migrated(clickhouse_container):
    """Apply migrations once for the whole module (default retention_days=7)."""
    host, port, user, pwd = clickhouse_container
    await _migrate(host, port, user, pwd, retention_days=7)
    return host, port, user, pwd


async def test_migrations_create_owned_tables(migrated):
    host, port, user, pwd = migrated
    client = await _client(host, port, user, pwd)
    try:
        result = await client.query(
            "SELECT name FROM system.tables WHERE database = 'ploston' "
            "AND name IN ('executions', 'steps', 'tool_calls') ORDER BY name"
        )
        names = {row[0] for row in result.result_rows}
        assert names == {"executions", "steps", "tool_calls"}
    finally:
        await client.close()


async def test_migrations_create_views(migrated):
    host, port, user, pwd = migrated
    client = await _client(host, port, user, pwd)
    try:
        result = await client.query(
            "SELECT name FROM system.tables WHERE database = 'ploston' "
            "AND engine = 'View' ORDER BY name"
        )
        names = {row[0] for row in result.result_rows}
        assert names == {"events", "traces"}
    finally:
        await client.close()


async def test_views_are_queryable_when_otel_tables_empty(migrated):
    """Spec requirement: VIEWs work even before OTEL exporter has written."""
    host, port, user, pwd = migrated
    client = await _client(host, port, user, pwd)
    try:
        events = await client.query("SELECT count() FROM ploston.events")
        traces = await client.query("SELECT count() FROM ploston.traces")
        assert events.result_rows[0][0] == 0
        assert traces.result_rows[0][0] == 0
    finally:
        await client.close()


async def test_tool_calls_has_expected_columns(migrated):
    host, port, user, pwd = migrated
    client = await _client(host, port, user, pwd)
    try:
        result = await client.query(
            "SELECT name FROM system.columns WHERE database = 'ploston' AND table = 'tool_calls'"
        )
        actual = {row[0] for row in result.result_rows}
    finally:
        await client.close()
    expected = {
        "execution_id",
        "step_id",
        "call_id",
        "tool_name",
        "started_at",
        "completed_at",
        "duration_ms",
        "params",
        "params_bytes",
        "result",
        "response_bytes",
        "error_code",
        "error_category",
        "error_message",
        "source",
        "runner_id",
        "bridge_id",
        "session_id",
        "sequence",
        "inserted_at",
    }
    assert expected.issubset(actual), expected - actual


async def test_migrations_are_idempotent(migrated):
    """Running twice produces no errors and no schema drift."""
    host, port, user, pwd = migrated
    await _migrate(host, port, user, pwd, retention_days=7)  # second pass
    client = await _client(host, port, user, pwd)
    try:
        result = await client.query("SELECT count() FROM system.tables WHERE database = 'ploston'")
        # 3 owned + 2 views + 2 OTEL stubs = 7
        assert result.result_rows[0][0] == 7
    finally:
        await client.close()


async def test_retention_substituted_into_engine_full(migrated):
    """Default fixture used retention_days=7 so engine_full must reflect that."""
    host, port, user, pwd = migrated
    client = await _client(host, port, user, pwd)
    try:
        result = await client.query(
            "SELECT engine_full FROM system.tables WHERE database = 'ploston' "
            "AND name = 'executions'"
        )
        engine_full = result.result_rows[0][0]
        # ClickHouse normalizes INTERVAL N DAY to toIntervalDay(N) in engine_full
        assert "toIntervalDay(7)" in engine_full or "INTERVAL 7 DAY" in engine_full
        assert "{{" not in engine_full
    finally:
        await client.close()
