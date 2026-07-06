---
name: ontology-extraction
description: Extract industrial object properties, enums, units, datatypes, keys, and constraints from raw schemas, payloads, documents, and tags. Use when candidate objects already exist and the task is to turn source fields into structured object definitions.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: extraction
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - candidate objects
      - source schemas
      - api payloads
      - tag lists
      - documents
    output_objects:
      - object property drafts
      - enum definitions
      - datatype notes
      - unit notes
      - constraint notes
    prerequisites:
      - ontology-discovery
    ai_capabilities:
      - llm-reasoning
      - structural-parsing
      - rules
    human_review: recommended
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology extraction
      - property extraction
      - enum and datatype extraction
---

# Ontology Extraction

Use this harness to convert source fields into ontology-ready object definitions.

## Working rules

1. Operate on candidate objects plus raw source evidence.
2. Extract properties, enums, units, datatypes, keys, constraints, and nullability separately.
3. Preserve the raw field name alongside the suggested business property name.
4. Distinguish observed fields from inferred business semantics.
5. Keep unresolved typing issues visible instead of guessing.

## Required outputs

- `objects`: object name, raw source labels, property list, key hints, and confidence.
- `properties`: canonical candidate name, raw field name, datatype, unit, enum values, and constraint notes.
- `typing_gaps`: fields that need domain review before release.

## Quality bar

- Promote `status` codes into enums when evidence exists.
- Extract units even when they are only implied by comments, manuals, or tag naming.
- Detect keys and uniqueness separately from relation discovery.
- Do not merge synonyms or rename aggressively here; pass that to `ontology-normalization`.
