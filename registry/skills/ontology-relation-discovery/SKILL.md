---
name: ontology-relation-discovery
description: Discover typed business relations between industrial objects from foreign keys, tag paths, document evidence, hierarchy structures, and semantic hints. Use when the task is to turn object co-occurrence into explicit relations such as belongsTo, generatedBy, uses, produces, or measuredBy.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: relation-discovery
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - candidate objects
      - extracted properties
      - foreign keys
      - tag paths
      - document references
      - hierarchy hints
    output_objects:
      - relation drafts
      - hierarchy drafts
      - relation evidence
      - cardinality notes
    prerequisites:
      - ontology-discovery
      - ontology-extraction
    ai_capabilities:
      - llm-reasoning
      - graph-building
      - rules
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology relation discovery
      - industrial graph building
      - hierarchy discovery
---

# Ontology Relation Discovery

Use this harness to convert source-level references into typed ontology relations.

## Working rules

1. Start from deterministic evidence such as foreign keys, path segments, document cross-references, and consistent naming patterns.
2. Prefer typed verbs such as `belongsTo`, `contains`, `generatedBy`, `uses`, `produces`, `consumes`, `measuredBy`, and `controlledBy`.
3. Capture relation evidence, direction, and cardinality with every relation.
4. Separate structural hierarchy from event or transaction relations.
5. Leave ambiguous links unresolved instead of inventing graph edges.

## Required outputs

- `relations`: source object, predicate, target object, evidence, confidence, and cardinality.
- `hierarchies`: site-to-workshop-to-line-to-machine or other validated structural chains.
- `relation_gaps`: unresolved references needing review or more evidence.

## Quality bar

- Promote `alarm.device_id -> Alarm generatedBy Machine` style patterns into business language only when evidence supports the semantics.
- Do not publish self-referential hierarchy loops without explicit justification.
- Keep raw key and path evidence so validation can trace every relation back to source data.
