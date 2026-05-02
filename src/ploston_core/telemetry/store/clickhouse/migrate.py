"""ClickHouse schema migration runner (S-295 / T-940).

Idempotent. Reads SQL files from `schema/` in numeric-prefix order, substitutes
`{{retention_days}}` from caller-supplied retention, and applies each statement
through clickhouse-connect.

The OTEL exporter (otel/opentelemetry-collector-contrib v0.105.0) creates
`otel_logs` and `otel_traces` lazily on first write. Our `010_views.sql`
references those tables, so this runner pre-creates them with the exporter's
own DDL — keeping `CREATE TABLE IF NOT EXISTS` semantics so the exporter
remains the source of truth at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

_SCHEMA_DIR = Path(__file__).parent / "schema"

# OTEL exporter v0.105.0 stub DDL (verbatim from
# opentelemetry-collector-contrib/exporter/clickhouseexporter@v0.105.0).
# Engine placeholders pre-baked for OSS single-node deployment.
_OTEL_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS {database}.otel_logs (
    Timestamp DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TimestampDate Date DEFAULT toDate(Timestamp),
    TimestampTime DateTime DEFAULT toDateTime(Timestamp),
    TraceId String CODEC(ZSTD(1)),
    SpanId String CODEC(ZSTD(1)),
    TraceFlags UInt8,
    SeverityText LowCardinality(String) CODEC(ZSTD(1)),
    SeverityNumber UInt8,
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    Body String CODEC(ZSTD(1)),
    ResourceSchemaUrl LowCardinality(String) CODEC(ZSTD(1)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeSchemaUrl LowCardinality(String) CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion LowCardinality(String) CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    LogAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_log_attr_key mapKeys(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_log_attr_value mapValues(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_body Body TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (ServiceName, TimestampDate, TimestampTime)
TTL TimestampTime + toIntervalDay({retention_days})
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;
"""

_OTEL_TRACES_DDL = """
CREATE TABLE IF NOT EXISTS {database}.otel_traces (
    Timestamp DateTime64(9) CODEC(Delta, ZSTD(1)),
    TraceId String CODEC(ZSTD(1)),
    SpanId String CODEC(ZSTD(1)),
    ParentSpanId String CODEC(ZSTD(1)),
    TraceState String CODEC(ZSTD(1)),
    SpanName LowCardinality(String) CODEC(ZSTD(1)),
    SpanKind LowCardinality(String) CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    SpanAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    Duration Int64 CODEC(ZSTD(1)),
    StatusCode LowCardinality(String) CODEC(ZSTD(1)),
    StatusMessage String CODEC(ZSTD(1)),
    Events Nested (
        Timestamp DateTime64(9),
        Name LowCardinality(String),
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD(1)),
    Links Nested (
        TraceId String,
        SpanId String,
        TraceState String,
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD(1)),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_span_attr_key mapKeys(SpanAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_span_attr_value mapValues(SpanAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_duration Duration TYPE minmax GRANULARITY 1
) ENGINE = MergeTree()
TTL toDateTime(Timestamp) + toIntervalDay({retention_days})
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SpanName, toUnixTimestamp(Timestamp), TraceId)
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;
"""


def _split_statements(sql: str) -> list[str]:
    """Split a multi-statement SQL string on top-level semicolons.

    Strips line comments (`--`) but keeps statement structure intact.
    """
    cleaned = re.sub(r"--[^\n]*", "", sql)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


async def run_migrations(
    host: str,
    port: int = 8123,
    database: str = "ploston",
    username: str = "default",
    password: str = "",
    secure: bool = False,
    retention_days: int = 7,
) -> None:
    """Apply ClickHouse schema migrations idempotently.

    Connects with ``database=None`` so the initial ``CREATE DATABASE``
    works against a fresh server. After the database exists, switches
    the client onto it for the remaining statements.
    """
    bootstrap_client: AsyncClient = await clickhouse_connect.get_async_client(
        host=host, port=port, username=username, password=password, secure=secure,
    )
    try:
        await bootstrap_client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    finally:
        await bootstrap_client.close()

    client: AsyncClient = await clickhouse_connect.get_async_client(
        host=host, port=port, database=database,
        username=username, password=password, secure=secure,
    )
    try:
        # OTEL exporter stubs first — VIEWs in 010_views.sql depend on them.
        for ddl in (_OTEL_LOGS_DDL, _OTEL_TRACES_DDL):
            await client.command(
                ddl.format(database=database, retention_days=retention_days)
            )
        for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
            sql = sql_file.read_text().replace(
                "{{retention_days}}", str(retention_days)
            )
            for stmt in _split_statements(sql):
                await client.command(stmt)
    finally:
        await client.close()

