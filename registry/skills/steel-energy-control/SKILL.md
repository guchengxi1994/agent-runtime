---
name: steel-energy-control
description: Harness skill for steel-industry energy control, energy balance, process-route diagnosis, benchmark research, and energy-saving action planning across integrated BF-BOF steelmaking, EAF steelmaking, coking, sintering, pelletizing, ironmaking, steelmaking, continuous casting, hot rolling, cold rolling, reheating furnaces, utilities, and steel-grade-specific production routes. Use when the user asks about steel plant energy consumption, energy control, energy balance, energy intensity, process optimization, carbon and fuel reduction, benchmark gaps, or selecting energy-saving measures.
metadata:
  owner: runtime
  category: steel-energy
  agent_runtime:
    capabilities:
      - steel energy control
      - process route diagnosis
      - energy balance
      - benchmark research
      - saving measure prioritization
---

# Steel Energy Control Harness

Use this harness to plan and execute steel energy-control work. Treat each executable skill as a callable capability; choose the smallest set that answers the user.

## Main routing logic

1. If the task is broad, ambiguous, or mentions only "steel energy", first identify the production route, product, process boundary, time basis, and available data. If these are missing and unsafe to assume, call `request_user_input`.
2. If the process route or data boundary is unclear, call `steel-process-map` to build the stage map, KPI list, required inputs, and candidate control levers.
3. If the user needs current external facts, benchmarks, policy values, market data, equipment norms, or citations, call `web-search`, then `web-fetch` on useful sources. If API search is unavailable, use `web-search-quark` as a no-key alternate.
4. If the user provides fuel, electricity, steam, recovered gas, by-product gas, or output data, call `steel-energy-balance` before giving conclusions.
5. If the user asks "what should we do", "how to reduce energy", "which measure first", or provides a measure list, call `steel-savings-prioritizer`.
6. If only simple arithmetic is needed and no steel-specific energy units are involved, use `calculator`.

## Minimum scoping fields

Ask only for fields that block the next step. Prefer assumptions when a useful first pass is possible.

Essential fields:

- `process_route`: one of `integrated-bf-bof`, `eaf`, `coking`, `sintering`, `pelletizing`, `blast-furnace`, `converter`, `continuous-casting`, `hot-rolling`, `cold-rolling`, `reheating-furnace`, `utilities`, or `unknown`.
- `product_or_grade`: examples include rebar, hot rolled coil, cold rolled sheet, stainless, silicon steel, bearing steel, or unknown.
- `boundary`: examples include whole plant, ironmaking, steelmaking, rolling line, reheating furnace, or one campaign.
- `basis`: examples include per tonne crude steel, per tonne slab, per tonne rolled product, per batch, per month, or annual.
- `available_data`: production tonnage, energy streams, gas recovery/export, temperatures, yield, operating rate, equipment constraints.

## Steel-domain reasoning checklist

Always separate the following instead of mixing them:

- Final energy versus primary energy.
- Purchased energy versus recovered by-product gas.
- Gross energy input versus net energy after export/recovery.
- Whole-plant energy intensity versus process-specific intensity.
- Crude steel basis versus finished product basis.
- Route effects, product/grade effects, equipment effects, and operation effects.
- Energy control actions that save energy from actions that only shift energy between carriers.

## Common route structure

For integrated BF-BOF steelmaking, examine raw material preparation, coking, sintering or pelletizing, blast furnace ironmaking, hot metal pretreatment, BOF steelmaking, secondary metallurgy, continuous casting, rolling or finishing, oxygen plant, power/steam/gas networks, and water systems.

For EAF steelmaking, examine scrap or DRI/HBI supply, preheating, EAF electricity and oxygen/fuel practice, foamy slag, tapping temperature, ladle furnace, casting, rolling, off-gas recovery, and power demand management.

For rolling, separate reheating furnace, descaling, roughing/finishing mills, laminar cooling, coiling, heat treatment, pickling, cold rolling, annealing, galvanizing, compressed air, hydraulics, water, and motor systems.

## Analysis pattern

Use this sequence unless the user asks for a narrow calculation:

1. Frame the boundary and normalize the KPI basis.
2. Map the route and major energy carriers with `steel-process-map`.
3. Gather missing external data with web skills if the answer requires current benchmarks or citations.
4. Compute energy balance and intensity with `steel-energy-balance` when numeric streams are available.
5. Identify dominant losses and controllable drivers.
6. Prioritize measures with `steel-savings-prioritizer`.
7. Produce an action plan with confidence levels and data gaps.

## Output format

For engineering users, prefer this structure:

- Scope and assumptions.
- Process boundary and energy-carrier map.
- Energy balance or KPI table.
- Main energy drivers and likely loss points.
- Candidate control measures ranked by impact, feasibility, risk, and payback.
- Data still needed to improve confidence.
- Next measurements or trials.

For management users, compress to:

- Baseline intensity.
- Top three controllable drivers.
- Savings range.
- Investment and operational priorities.
- Risks and prerequisites.

## Domain heuristics

Use these as qualitative heuristics only; verify with plant data or external sources when decisions are high impact.

- In integrated steelmaking, the largest energy-control leverage is usually in coke rate, blast furnace fuel rate, hot metal ratio, gas recovery/use, sinter quality, oxygen enrichment, heat recovery, and rolling reheating control.
- In EAF steelmaking, the major levers are metallic charge quality, scrap preheating, electrical practice, oxygen/fuel balance, foamy slag stability, tap-to-tap time, transformer utilization, and off-gas heat recovery.
- In rolling, reheating furnace control, hot charging/direct rolling, scale loss, mill motor efficiency, compressed air leakage, cooling water, and yield often dominate controllable energy.
- High-alloy, stainless, silicon steel, bearing steel, and heat-treated products can have higher downstream and finishing energy than commodity carbon steel; do not compare them on the same basis without adjustment.
- Recovered BFG/COG/LDG and steam can make apparent process energy look low or high depending on accounting. Always state the accounting method.

## Final answer quality bar

Do not present a single universal steel energy benchmark unless the route, product, and boundary match. If evidence is mixed, explain why: route mix, product mix, accounting boundary, age of equipment, fuel prices, region, environmental constraints, or data source method.

When using artifacts, distinguish successful evidence artifacts from failed or empty attempts. If a source or fetch failed but other artifacts succeeded, use the successful artifacts and cite their artifact ids.
