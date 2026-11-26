#!/bin/bash
# Metrics tables for Claude Code metrics (GUARD-3 T4). Same database as the events, same 180-day TTL.
# Table layout = what the ClickHouse exporter (v0.165.0) creates itself with create_schema: true; it was generated
# once in a scratch database and copied here, so the collector needs no CREATE right at run time (create_schema: false).
# Only sum and gauge exist: Claude Code sends counters (sums). A histogram, exponential histogram or summary would be
# refused by the exporter (no table) until a table for it is added here.
#
# Idempotent (IF NOT EXISTS, GRANT again changes nothing). It runs on its own in the init folder (empty volume) and by
# hand on a stack that already has data:
#     docker compose exec -T clickhouse bash /docker-entrypoint-initdb.d/02-metrics.sh
set -euo pipefail

COMMON="
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    ResourceSchemaUrl String CODEC(ZSTD(3)),
    ScopeName String CODEC(ZSTD(3)),
    ScopeVersion String CODEC(ZSTD(3)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(3)),
    ScopeSchemaUrl String CODEC(ZSTD(3)),
    ServiceName LowCardinality(String) CODEC(ZSTD(3)),
    MetricName LowCardinality(String) CODEC(ZSTD(3)),
    MetricDescription String CODEC(ZSTD(3)),
    MetricUnit String CODEC(ZSTD(3)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    StartTimeUnix DateTime CODEC(Delta(4), ZSTD(3)),
    TimeUnix DateTime CODEC(Delta(4), ZSTD(3)),
    Value Float64 CODEC(ZSTD(3)),
    Flags UInt32 CODEC(ZSTD(3)),
    \`Exemplars.FilteredAttributes\` Array(Map(LowCardinality(String), String)) CODEC(ZSTD(3)),
    \`Exemplars.TimeUnix\` Array(DateTime) CODEC(ZSTD(3)),
    \`Exemplars.Value\` Array(Float64) CODEC(ZSTD(3)),
    \`Exemplars.SpanId\` Array(String) CODEC(ZSTD(3)),
    \`Exemplars.TraceId\` Array(String) CODEC(ZSTD(3))"
TAIL="
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_time_minmax TimeUnix TYPE minmax GRANULARITY 1
) ENGINE = MergeTree
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, toStartOfHour(TimeUnix), cityHash64(Attributes), TimeUnix)
TTL toDateTime(TimeUnix) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1"

clickhouse client -n <<SQL
CREATE TABLE IF NOT EXISTS guard.otel_metrics_sum ($COMMON,
    AggregationTemporality Int32 CODEC(ZSTD(3)),
    IsMonotonic Bool CODEC(Delta(1), ZSTD(3)),
$TAIL;

CREATE TABLE IF NOT EXISTS guard.otel_metrics_gauge ($COMMON,
$TAIL;

GRANT INSERT ON guard.otel_metrics_sum TO otel_writer;
GRANT INSERT ON guard.otel_metrics_gauge TO otel_writer;
-- grafana_reader already has SELECT on guard.* (01-schema.sh)
SQL
