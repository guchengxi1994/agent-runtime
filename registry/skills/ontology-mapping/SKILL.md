---
name: ontology-mapping
description: Map canonical ontology objects and properties to runtime sources such as SQL tables, API endpoints, PLC registers, MQTT topics, OPC UA nodes, files, and document anchors. Use when the task is to connect ontology terms like Machine.temperature to the real source addresses that supply or accept data.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: mapping
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - canonical ontology
      - source schemas
      - api specs
      - tag addresses
      - topic names
      - node ids
    output_objects:
      - runtime mappings
      - transformation rules
      - read write policies
      - freshness notes
    prerequisites:
      - ontology-normalization
    ai_capabilities:
      - llm-reasoning
      - rules
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology mapping
      - source mapping
      - industrial runtime binding
---

# Ontology Mapping

Use this harness to connect canonical ontology fields to concrete runtime sources.

## Working rules

1. Keep the ontology stable and map sources to it; do not let physical addresses rename the ontology.
2. Capture source kind, source path, direction, freshness, and transformation logic for every mapping.
3. Allow multiple physical mappings for one canonical field when precedence or fallback is defined.
4. Separate read mappings, write mappings, and derived mappings.
5. Record security or approval constraints for write paths.

## Required outputs

- `mappings`: canonical object, property or behavior, source kind, source path, direction, and transformation notes.
- `runtime_policies`: precedence, caching, freshness, and failure handling.
- `mapping_gaps`: canonical fields that still lack a valid runtime source.

## Quality bar

- A mapping is incomplete if it lacks source ownership, direction, or transformation notes.
- Avoid embedding units, alias resolution, or business semantics only inside mapping rules. Normalize them first.
- Treat write mappings for PLC, API, or workflow actions as safety-sensitive artifacts.
