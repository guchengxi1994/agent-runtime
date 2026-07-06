---
name: ontology-publish
description: Prepare industrial ontology releases with versioning, impact analysis, migration notes, and rollback planning. Use when the task is to publish a reviewed ontology increment, compare versions, or understand how changes affect workflows, agents, dashboards, queries, and runtime mappings.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: publish
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - validated ontology
      - change set
      - runtime consumers
      - mappings
      - behaviors
    output_objects:
      - release package
      - version diff
      - impact matrix
      - migration notes
      - rollback plan
    prerequisites:
      - ontology-validation
    ai_capabilities:
      - llm-reasoning
      - rules
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology publish
      - ontology versioning
      - ontology impact analysis
---

# Ontology Publish

Use this harness to turn a validated ontology change into a governed release.

## Working rules

1. Version ontology changes explicitly using `major.minor.patch`.
2. Classify each change as additive, compatible refinement, or breaking.
3. Analyze impact on workflows, agents, dashboards, queries, and runtime mappings.
4. Generate migration notes and rollback notes before release.
5. Block publish if unresolved validation errors remain.

## Required outputs

- `release_version`: proposed new version and rationale.
- `change_summary`: objects, properties, relations, behaviors, and mappings added, changed, or removed.
- `impact_matrix`: dependent assets and affected areas.
- `migration_notes`: what downstream consumers must change.
- `rollback_plan`: how to restore the previous release safely.

## Quality bar

- A new property like `Machine.voltage` is not just a schema change; assess dashboards, queries, and workflows that may need updates.
- Breaking changes need both migration notes and a rollback path.
- Release only reviewed ontology states, never draft inference directly.
