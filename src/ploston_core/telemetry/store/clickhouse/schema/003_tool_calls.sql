-- S-295 / T-939: Per-tool-call detail.
-- Owned table: schema controlled by ClickHouseTelemetryStore.
-- Bloom-filter indexes on session_id, runner_id, call_id support the
-- Session Inspector (S-299) drill-down panels.
CREATE TABLE IF NOT EXISTS ploston.tool_calls (
    execution_id   String,
    step_id        String,
    call_id        String,
    tool_name      String,
    started_at     DateTime64(6),
    completed_at   Nullable(DateTime64(6)),
    duration_ms    Nullable(UInt64),

    params         String CODEC(ZSTD(3)),
    params_bytes   UInt64 DEFAULT 0,
    result         String CODEC(ZSTD(3)),
    response_bytes UInt64 DEFAULT 0,

    error_code     Nullable(String),
    error_category Nullable(String),
    error_message  Nullable(String) CODEC(ZSTD(3)),

    source         LowCardinality(String),
    runner_id      Nullable(String),
    bridge_id      Nullable(String),
    session_id     Nullable(String),
    sequence       UInt32,

    inserted_at    DateTime64(3) DEFAULT now64(3),

    INDEX idx_tool    tool_name  TYPE set(1000)      GRANULARITY 4,
    INDEX idx_session session_id TYPE bloom_filter   GRANULARITY 4,
    INDEX idx_runner  runner_id  TYPE bloom_filter   GRANULARITY 4,
    INDEX idx_call_id call_id    TYPE bloom_filter   GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(started_at)
ORDER BY (started_at, session_id, tool_name)
TTL toDateTime(started_at) + INTERVAL {{retention_days}} DAY
SETTINGS allow_nullable_key = 1, index_granularity = 8192;

