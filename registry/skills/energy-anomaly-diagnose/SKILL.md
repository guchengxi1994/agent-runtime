---
name: energy-anomaly-diagnose
description: Diagnose industrial energy anomalies from resolved telemetry, baseline windows, events, work-order context, and metric semantics, returning anomaly severity, candidate causes, and an evidence chain. Use when the user asks why a line, machine, or process segment showed abnormal power, energy, or specific-energy behavior during a given time range.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: diagnosis
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        entity_ref:
          type: string
          description: Target line, machine, or process entity reference.
        metric_name:
          type: string
          description: Metric under diagnosis such as power_kw, energy_kwh, or specific_energy_kwh_per_t.
        current_window:
          type: object
          description: Current analysis window containing timeseries and optional events.
          additionalProperties: true
        baseline_window:
          type: object
          description: Baseline window containing comparable timeseries and optional events.
          additionalProperties: true
        context:
          type: object
          description: Optional resolved ontology context such as mappings, related entities, or metric definitions.
          additionalProperties: true
        thresholds:
          type: object
          description: Optional threshold overrides such as spike_ratio or deviation_sigma.
          additionalProperties: true
      required:
        - entity_ref
        - metric_name
        - current_window
      additionalProperties: false
    execution_policy:
      timeout_ms: 45000
      idle_timeout_ms: 15000
      packages: []
---

# Energy Anomaly Diagnose

Use this executable skill after the runtime has already resolved the target entity and gathered telemetry slices.

The skill is deterministic and evidence-oriented. It should complement, not replace, a harness that chooses the right windows and asks for missing business context.
