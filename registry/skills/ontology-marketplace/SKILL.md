---
name: ontology-marketplace
description: Reuse industrial ontology starter templates and marketplace entries instead of rebuilding common domains from scratch. Use when the task is to bootstrap a manufacturing, new-energy, battery, steel, chemical, food and beverage, or industrial park ontology and then adapt it with source-specific discovery and mapping.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: marketplace
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - industry scope
      - domain template request
      - existing ontology fragments
    output_objects:
      - template selection
      - bootstrap ontology
      - extension plan
    prerequisites: []
    ai_capabilities:
      - llm-reasoning
      - template-selection
    human_review: recommended
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology marketplace
      - industry template reuse
      - ontology bootstrap
---

# Ontology Marketplace

Use this harness to start from a reusable industry ontology template and customize only the delta.

## Working rules

1. Call `ontology-template-marketplace` to get the closest starter model.
2. Pick the narrowest template that still covers the business scope.
3. Preserve which template elements were reused, extended, overridden, or removed.
4. Use later pipeline stages to adapt the template to real source evidence.
5. Keep template reuse explicit so future upgrades can compare customer customizations against the base template.

## Recommended template families

- `generic-manufacturing`
- `new-energy`
- `battery`
- `steel`
- `chemical`
- `food-beverage`
- `industrial-park`

## Quality bar

- Template reuse should reduce modeling effort, not freeze the ontology into a rigid vendor schema.
- Do not skip discovery and mapping after template selection.
- Record which canonical objects are inherited versus site-specific.
