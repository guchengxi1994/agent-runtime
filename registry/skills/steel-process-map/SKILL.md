---
name: steel-process-map
description: Build a structured steel production route map for energy-control work. Use to identify process stages, energy carriers, controllable energy drivers, required measurement data, KPI boundaries, and likely energy-saving levers for BF-BOF, EAF, coking, sintering, pelletizing, blast furnace, converter, continuous casting, hot rolling, cold rolling, reheating furnace, and utility-system analyses.
metadata:
  owner: runtime
  category: steel-energy
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        process_route:
          type: string
          description: Production route or process boundary, for example integrated-bf-bof, eaf, hot-rolling, cold-rolling, reheating-furnace, blast-furnace, converter, utilities, or unknown.
          default: unknown
        product_or_grade:
          type: string
          description: Product or steel grade family, for example rebar, hot rolled coil, stainless, silicon steel, bearing steel, or unknown.
          default: unknown
        boundary:
          type: string
          description: Accounting boundary such as whole plant, process line, furnace, campaign, monthly operation, or unknown.
          default: unknown
        objective:
          type: string
          description: User goal such as baseline diagnosis, energy balance, benchmark gap, saving plan, control strategy, or unknown.
          default: baseline diagnosis
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
    required_secrets: {}
---

# Steel Process Map

Use this executable skill when the agent needs a steel-route map before calculation, benchmark research, or control planning.

The output is a structured process map with stages, carriers, drivers, measurement needs, KPIs, and follow-up skill suggestions. It is intentionally generic and must be refined with plant data or web evidence for high-stakes decisions.
