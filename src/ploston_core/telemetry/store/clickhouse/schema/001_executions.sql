-- S-295 / T-939: Top-level execution records.
-- Owned table: schema controlled by ClickHouseTelemetryStore.
-- Engine choice locked: pure MergeTree + delete-then-insert at the application layer.
CREATE TABLE IF NOT EXISTS ploston.executions (
    execution_id      String,
    execution_type    LowCardinality(String),
    workflow_id       Nullable(String),
    workflow_version  Nullable(String),
    tool_name         Nullable(String),
    status            LowCardinality(String),
    started_at        DateTime64(6),
    completed_at      Nullable(DateTime64(6)),
    duration_ms       Nullable(UInt64),

    inputs            String CODEC(ZSTD(3)),
    outputs           String CODEC(ZSTD(3)),
    inputs_bytes      UInt64 DEFAULT 0,
    outputs_bytes     UInt64 DEFAULT 0,

    error_code        Nullable(String),
    error_category    Nullable(String),
    error_message     Nullable(String) CODEC(ZSTD(3)),

    source            LowCardinality(String),
    caller_id         Nullable(String),
    tenant_id         Nullable(String),
    session_id        Nullable(String),
    runner_id         Nullable(String),
    bridge_session_id Nullable(String),

    step_count           UInt32 DEFAULT 0,
    tool_call_count      UInt32 DEFAULT 0,
    total_response_bytes UInt64 DEFAULT 0,

    inserted_at       DateTime64(3) DEFAULT now64(3),

    INDEX idx_workflow workflow_id TYPE bloom_filter GRANULARITY 4,
    INDEX idx_session  session_id  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_runner   runner_id   TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(started_at)
ORDER BY (started_at, execution_type, execution_id)
TTL toDateTime(started_at) + INTERVAL {{retention_days}} DAY
SETTINGS index_granularity = 8192;

