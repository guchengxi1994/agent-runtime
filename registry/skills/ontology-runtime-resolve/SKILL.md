---
name: ontology-runtime-resolve
description: Resolve an industrial entity reference, metric, and analysis intent against the current workspace ontology registry, returning candidate instances, mapped source paths, related meters or sensors, relevant tables, and nearby alarms or work-order context. Use when a harness needs to convert business language such as 3号产线、主轧机、功率、单吨电耗 into concrete runtime data bindings before querying or diagnosing.
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
        entity_ref:
          type: string
          description: Business entity reference, instance ref, code, or display name such as line::line3 or 3号产线.
        metric_name:
          type: string
          description: Optional metric name such as power_kw, energy_kwh, temperature, or speed.
        analysis_goal:
          type: string
          description: Optional intent such as anomaly_diagnosis, mapping_review, or source_discovery.
        include_related:
          type: boolean
          description: When true, include nearby entities, instance relations, and relevant event context.
          default: true
      required:
        - entity_ref
      additionalProperties: false
    execution_policy:
      timeout_ms: 45000
      idle_timeout_ms: 15000
      packages: []
---

# Ontology Runtime Resolve

Use this executable skill before data queries when business references must be grounded into actual instances, points, tables, or source paths.

If resolution is ambiguous, surface the best candidates with confidence notes instead of pretending there is only one answer.
