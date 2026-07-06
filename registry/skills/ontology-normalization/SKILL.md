---
name: ontology-normalization
description: Normalize industrial ontology names, aliases, datatypes, and units across inconsistent source vocabularies such as Device, Equipment, Machine, Temp, TMP, or equipment temperature labels. Use when the task is to merge aliases into canonical objects and properties without losing source evidence.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: normalization
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - candidate objects
      - extracted properties
      - relation drafts
      - aliases
      - unit variants
    output_objects:
      - canonical object dictionary
      - canonical property dictionary
      - unit normalization rules
      - merge rationale
    prerequisites:
      - ontology-discovery
      - ontology-extraction
    ai_capabilities:
      - llm-reasoning
      - rules
      - standards-mapping
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology normalization
      - alias merging
      - unit normalization
---

# Ontology Normalization

Use this harness to turn source-specific vocabularies into canonical ontology names.

## Working rules

1. Keep raw aliases, canonical names, and display labels as separate fields.
2. Normalize object names, property names, units, and core datatypes.
3. Merge synonyms only when the business meaning matches across sources.
4. Record the rationale for every merge, split, or rejected merge.
5. If industry standards matter, align canonical names to ISA-95, AAS, OPC UA companion models, or the relevant domain standard.

## Required outputs

- `canonical_objects`: object name, aliases, source coverage, and merge rationale.
- `canonical_properties`: canonical property name, raw aliases, datatype, normalized unit, and evidence.
- `normalization_conflicts`: unresolved collisions such as one alias mapping to multiple meanings.

## Quality bar

- Normalize `Device`, `Equipment`, `Machine`, `设备`, and `机台` only when they refer to the same business class in scope.
- Standardize engineering units explicitly instead of burying conversion logic inside mappings.
- Avoid irreversible merges when two source terms may diverge later at runtime.
