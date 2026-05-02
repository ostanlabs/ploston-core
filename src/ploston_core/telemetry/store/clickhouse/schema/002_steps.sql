-- S-295 / T-939: Per-step records within a workflow execution.
-- Owned table: schema controlled by ClickHouseTelemetryStore.
-- PARTITION BY uses inserted_at (non-null) instead of nullable started_at.
CREATE TABLE IF NOT EXISTS ploston.steps (
    execution_id   String,
    step_id        String,
    attempt        UInt8 DEFAULT 1,
    step_type      LowCardinality(String),
    status         LowCardinality(String),
    skip_reason    Nullable(String),
    started_at     Nullable(DateTime64(6)),
    completed_at   Nullable(DateTime64(6)),
    duration_ms    Nullable(UInt64),
    tool_name      Nullable(String),
    tool_params    String CODEC(ZSTD(3)),
    tool_result    String CODEC(ZSTD(3)),
    code_hash      Nullable(String),
    error_code     Nullable(String),
    error_message  Nullable(String) CODEC(ZSTD(3)),
    max_attempts   UInt8 DEFAULT 1,

    inserted_at    DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(inserted_at)
ORDER BY (execution_id, step_id, attempt)
TTL toDateTime(inserted_at) + INTERVAL {{retention_days}} DAY;

