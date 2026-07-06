---
name: ontology-template-marketplace
description: Return reusable industrial ontology starter templates for generic manufacturing, new energy, battery, steel, chemical, food and beverage, and industrial park scenarios. Use when the task is to bootstrap an ontology marketplace entry, choose a domain template, or avoid rebuilding common objects, relations, behaviors, and knowledge anchors from scratch.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: marketplace
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - industry scope
      - domain request
      - optional scope focus
    output_objects:
      - ontology template
      - candidate objects
      - candidate relations
      - extension guidance
    prerequisites: []
    ai_capabilities:
      - deterministic-template-selection
    human_review: recommended
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        industry:
          type: string
          description: Industry template to bootstrap, for example generic-manufacturing, new-energy, battery, steel, chemical, food-beverage, or industrial-park.
          default: generic-manufacturing
        scope:
          type: string
          description: Modeling focus such as enterprise, plant, line, machine, or utility.
          default: plant
        include_behaviors:
          type: boolean
          description: Whether to include suggested object behaviors in the result.
          default: true
        include_mappings:
          type: boolean
          description: Whether to include recommended runtime mapping anchor types in the result.
          default: true
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Ontology Template Marketplace

Use this executable skill to bootstrap a reusable industrial ontology template before doing source-specific discovery and mapping.

The result is a starter model, not a published ontology. Extend or prune it with `ontology-discovery`, `ontology-normalization`, `ontology-mapping`, and `ontology-validation`.
