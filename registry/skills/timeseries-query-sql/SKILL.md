---
name: timeseries-query-sql
description: Query workspace-scoped sample timeseries and event data, or an explicitly provided SQLite database, using entity, metric, time range, and aggregation hints. Use when the agent has already resolved a business entity and now needs deterministic read-only telemetry or event slices for analysis, especially for industrial energy anomaly workflows.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: runtime
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        workspace_id:
          type: string
          description: Optional workspace id override. Omit to use sandbox context.
        db_path:
          type: string
          description: Optional absolute database path override.
        source_db_path:
          type: string
          description: Optional external SQLite database to query in read-only mode.
        entity_ref:
          type: string
          description: Entity reference resolved from ontology-runtime-resolve.
        metric_name:
          type: string
          description: Metric name such as power_kw, energy_kwh, temperature, or speed.
        start_time:
          type: string
          description: Inclusive start time in ISO-like format.
        end_time:
          type: string
          description: Inclusive end time in ISO-like format.
        aggregate:
          type: string
          description: Optional aggregate mode such as raw, avg, sum, max, min, latest.
          default: raw
        limit:
          type: integer
          description: Maximum number of rows to return for raw queries.
          default: 200
        include_events:
          type: boolean
          description: When true, also return event rows that overlap the requested window.
          default: true
        sql_query:
          type: string
          description: Optional explicit SQL query for advanced read-only SQLite use.
      required:
        - entity_ref
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 20000
      packages: []
---

# Timeseries Query SQL

Use this executable skill as the deterministic read layer after business references have been resolved.

Prefer structured parameters over `sql_query`. Use explicit SQL only when a harness has a strong reason and the source is known to be SQLite-compatible.
