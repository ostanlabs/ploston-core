-- S-295 / T-939: SQL VIEWs over OTEL Collector exporter tables.
-- The OTEL `clickhouse` exporter owns otel_logs and otel_traces.
-- These VIEWs surface our preferred column naming on top — zero storage cost.
CREATE VIEW IF NOT EXISTS ploston.events AS
SELECT
    Timestamp                              AS timestamp,
    SeverityText                           AS severity,
    SeverityNumber                         AS severity_number,
    coalesce(LogAttributes['component'],
             ScopeName)                    AS component,
    Body                                   AS message,
    TraceId                                AS trace_id,
    SpanId                                 AS span_id,
    LogAttributes['execution_id']          AS execution_id,
    LogAttributes['session_id']            AS session_id,
    LogAttributes['runner_id']             AS runner_id,
    LogAttributes['source']                AS source,
    LogAttributes['event']                 AS event,
    LogAttributes                          AS attributes,
    ServiceName                            AS service_name
FROM ploston.otel_logs;

CREATE VIEW IF NOT EXISTS ploston.traces AS
SELECT
    Timestamp                                          AS start_time,
    Timestamp + toIntervalNanosecond(Duration)         AS end_time,
    Duration                                           AS duration_ns,
    Duration / 1000000                                 AS duration_ms,
    TraceId                                            AS trace_id,
    SpanId                                             AS span_id,
    ParentSpanId                                       AS parent_span_id,
    SpanName                                           AS operation_name,
    SpanKind                                           AS span_kind,
    ServiceName                                        AS service_name,
    StatusCode                                         AS status_code,
    StatusMessage                                      AS status_message,
    SpanAttributes['tool.name']                        AS tool_name,
    SpanAttributes['tool.source']                      AS tool_source,
    SpanAttributes['runner.id']                        AS runner_id,
    SpanAttributes['bridge.id']                        AS bridge_id,
    SpanAttributes                                     AS attributes
FROM ploston.otel_traces;

