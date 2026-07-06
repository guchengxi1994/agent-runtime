---
name: ontology-registry-upsert
description: Persist industrial ontology fragments, runtime mappings, entity instances, metric definitions, and sample telemetry or event data into the current workspace ontology database. Use when the agent has extracted objects, relations, mappings, SQL schema clues, tag CSV, or sample data from documents and must turn them into reusable runtime state for later resolve, query, and anomaly diagnosis.
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
          description: Optional workspace id override. Omit to use the current workspace context.
        db_path:
          type: string
          description: Optional absolute database path override for advanced use.
        ontology:
          type: object
          description: Structured ontology fragment extracted by a harness skill or the model.
          additionalProperties: true
        instances:
          type: array
          description: Optional entity instances to persist.
          items:
            type: object
            additionalProperties: true
        instance_relations:
          type: array
          description: Optional instance-level relations such as line contains machine.
          items:
            type: object
            additionalProperties: true
        metric_definitions:
          type: array
          description: Optional metric semantic definitions such as power_kw or energy_kwh.
          items:
            type: object
            additionalProperties: true
        evidence:
          type: array
          description: Optional supporting evidence snippets or source references.
          items:
            type: object
            additionalProperties: true
        source_catalog:
          type: array
          description: Optional source definitions such as sqlite tables or API bindings.
          items:
            type: object
            additionalProperties: true
        schema_sql:
          type: string
          description: Optional SQL DDL text to parse into source structure and inferred ontology hints.
        tag_csv:
          type: string
          description: Optional CSV text with PLC, MQTT, or OPC UA tag rows.
        timeseries_csv:
          type: string
          description: Optional CSV text with sample timeseries rows.
        events_csv:
          type: string
          description: Optional CSV text with sample event rows.
        timeseries_rows:
          type: array
          description: Optional sample timeseries rows already structured as JSON objects.
          items:
            type: object
            additionalProperties: true
        event_rows:
          type: array
          description: Optional sample event rows already structured as JSON objects.
          items:
            type: object
            additionalProperties: true
        auto_infer_from_schema:
          type: boolean
          description: When true, infer object and relation hints from schema_sql when possible.
          default: true
        auto_infer_from_tags:
          type: boolean
          description: When true, infer line, machine, point, and metric hints from tag_csv when possible.
          default: true
      additionalProperties: false
    execution_policy:
      timeout_ms: 120000
      idle_timeout_ms: 30000
      packages: []
---

# Ontology Registry Upsert

Use this executable skill to convert extracted ontology and source hints into workspace-persistent runtime state.

Prefer passing structured `ontology`, `instances`, and `metric_definitions` when a harness already extracted them. Use `schema_sql`, `tag_csv`, `timeseries_csv`, or `events_csv` as deterministic enrichment inputs, not as a substitute for good harness planning.
