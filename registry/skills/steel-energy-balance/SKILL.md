---
name: steel-energy-balance
description: Calculate steel-process energy balance, net energy input, recovered or exported energy, GJ per tonne, kgce per tonne, electricity share, fuel share, and data-quality flags from provided energy streams. Use for steel plant, BF-BOF, EAF, rolling, reheating furnace, utility, or line-level energy calculations when quantities and units are available.
metadata:
  owner: runtime
  category: steel-energy
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        production_tonnes:
          type: number
          description: Production quantity for the selected boundary and period.
        product_basis:
          type: string
          description: Basis of production_tonnes, for example crude steel, hot metal, slab, billet, rolled product, or processed coil.
          default: selected product
        energy_streams:
          type: array
          description: Energy streams in the boundary.
          items:
            type: object
            properties:
              name:
                type: string
                description: Energy stream name.
              amount:
                type: number
                description: Numeric amount.
              unit:
                type: string
                description: Unit. Supported examples include GJ, MJ, MWh, kWh, tce, kgce, tonne-coal-equivalent, tonne-coke, kg-coke, Nm3-natural-gas, 1000Nm3-natural-gas, 1000Nm3-BFG, 1000Nm3-COG, 1000Nm3-LDG.
              role:
                type: string
                description: input, recovered, exported, loss, output, or credit. Inputs and losses add to gross input; recovered/exported/output/credit are reported and subtract from net input by default.
                default: input
              category:
                type: string
                description: electricity, fuel, steam, oxygen, gas, recovered, utility, material, or other.
                default: other
              lhv_gj_per_unit:
                type: number
                description: Optional conversion factor to GJ per unit for site-specific fuels.
            required:
              - name
              - amount
              - unit
            additionalProperties: false
        electricity_primary_factor:
          type: number
          description: Optional primary energy factor applied to electricity final energy. If omitted, final energy is used.
        subtract_recovered_from_net:
          type: boolean
          description: Whether recovered/exported/output/credit streams subtract from net input.
          default: true
      required:
        - production_tonnes
        - energy_streams
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
    required_secrets: {}
---

# Steel Energy Balance

Use this executable skill for deterministic energy conversion and intensity calculations.

Provide site-specific LHV or conversion factors whenever available. Built-in gas and solid-fuel conversion factors are generic engineering defaults and should be treated as estimates.
