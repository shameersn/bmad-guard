#!/bin/bash
# The admin creates the database, the events table and two least-privilege users.
# Table DDL follows the exporter's logs_table.sql (v0.165.0), minus the k8s columns,
# plus materialized columns for the guard fields we query most.
set -euo pipefail

clickhouse client -n <<SQL
CREATE DATABASE IF NOT EXISTS guard;

CREATE TABLE IF NOT EXISTS guard.guard_events (
    Timestamp DateTime64(9) CODEC(Delta(8), ZSTD(3)),
    TraceId String CODEC(ZSTD(3)),
    SpanId String CODEC(ZSTD(3)),
    TraceFlags UInt8,
    SeverityText LowCardinality(String) CODEC(ZSTD(3)),
    SeverityNumber UInt8,
    ServiceName LowCardinality(String) CODEC(ZSTD(3)),
    Body String CODEC(ZSTD(3)),
    ResourceSchemaUrl LowCardinality(String) CODEC(ZSTD(3)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    ScopeSchemaUrl LowCardinality(String) CODEC(ZSTD(3)),
    ScopeName String CODEC(ZSTD(3)),
    ScopeVersion LowCardinality(String) CODEC(ZSTD(3)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    LogAttributes Map(LowCardinality(String), String) CODEC(ZSTD(3)),
    EventName String CODEC(ZSTD(3)),
    User LowCardinality(String) MATERIALIZED LogAttributes['user'],
    Guard LowCardinality(String) MATERIALIZED LogAttributes['guard'],
    Verdict LowCardinality(String) MATERIALIZED LogAttributes['verdict'],
    Project LowCardinality(String) MATERIALIZED LogAttributes['project'],
    EventId String MATERIALIZED LogAttributes['event_id'],
    INDEX idx_log_attr_key mapKeys(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_log_attr_value mapValues(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp)
TTL toDateTime(Timestamp) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;

CREATE USER IF NOT EXISTS otel_writer IDENTIFIED WITH sha256_password BY '${OTEL_WRITER_PASSWORD}';
GRANT INSERT ON guard.guard_events TO otel_writer;

CREATE USER IF NOT EXISTS grafana_reader IDENTIFIED WITH sha256_password BY '${GRAFANA_READER_PASSWORD}'
    SETTINGS readonly = 2;  -- 2 = no writes, but the Grafana plugin may set query settings (readonly = 1 breaks it)
GRANT SELECT ON guard.* TO grafana_reader;
SQL
