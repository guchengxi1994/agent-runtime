---
name: steel-savings-prioritizer
description: Rank steel energy-saving and energy-control measures by estimated annual energy saving, cost saving, payback, feasibility, risk, evidence strength, and implementation speed. Use for BF-BOF, EAF, rolling, reheating furnace, utility, gas recovery, compressed-air, steam, motor, and process-control improvement plans when candidate measures or saving assumptions are available.
metadata:
  owner: runtime
  category: steel-energy
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        baseline_energy_gj_per_year:
          type: number
          description: Optional baseline annual energy use for deriving savings from percentages.
        energy_price_per_gj:
          type: number
          description: Blended energy value used for simple economic ranking.
          default: 0
        measures:
          type: array
          description: Candidate energy-control measures.
          items:
            type: object
            properties:
              name:
                type: string
                description: Measure name.
              process_stage:
                type: string
                description: Process stage or utility system.
              annual_energy_saving_gj:
                type: number
                description: Estimated annual final-energy saving.
              saving_percent:
                type: number
                description: Percent of baseline energy saved if annual_energy_saving_gj is not provided.
              capex:
                type: number
                description: Investment cost in the user's currency.
                default: 0
              annual_opex_saving:
                type: number
                description: Non-energy annual operating-cost saving in the user's currency.
                default: 0
              implementation_months:
                type: number
                description: Estimated implementation duration.
                default: 6
              evidence_level:
                type: string
                description: high, medium, low, or unknown.
                default: unknown
              risk_level:
                type: string
                description: low, medium, high, or unknown.
                default: unknown
              dependencies:
                type: array
                items:
                  type: string
                description: Prerequisites or constraints.
            required:
              - name
            additionalProperties: false
        objective:
          type: string
          description: Ranking objective such as quick wins, maximum savings, low risk, or balanced.
          default: balanced
      required:
        - measures
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
    required_secrets: {}
---

# Steel Savings Prioritizer

Use this executable skill after candidate energy-control measures are identified. It produces a transparent ranking, not a final investment decision.

Interpret results with engineering judgment. Measures that affect quality, safety, emissions, or bottleneck capacity require plant review even if the score is high.
