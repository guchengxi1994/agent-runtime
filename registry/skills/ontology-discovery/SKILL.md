---
name: ontology-discovery
description: Discover candidate industrial objects, source boundaries, and source inventories from databases, APIs, documents, PLC tags, OPC UA nodes, MQTT topics, logs, directories, and configuration files. Use when the task is to identify what real-world objects exist before extraction, normalization, mapping, or behavior modeling.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: discovery
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - databases
      - api specs
      - documents
      - plc tags
      - opc ua nodes
      - mqtt topics
      - logs
      - directories
      - configs
    output_objects:
      - candidate objects
      - source inventory
      - boundary notes
      - source evidence
    prerequisites: []
    ai_capabilities:
      - llm-reasoning
      - pattern-recognition
    human_review: recommended
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology discovery
      - candidate object discovery
      - industrial source inventory
---

# Ontology Discovery

Use this harness to discover candidate industrial objects before making any canonical ontology commitments.

## Working rules

1. Start from the business boundary: site, workshop, line, machine family, utility system, or enterprise scope.
2. Inventory source families first: database, API, document, PLC, OPC UA, MQTT, directory, log, and config.
3. Extract candidate nouns from table names, endpoint names, document titles, tag prefixes, alarm topics, and folder structure.
4. Preserve raw names and aliases exactly as found. Do not normalize yet.
5. Emit candidate objects with source evidence and confidence instead of claiming they are final ontology classes.

## Required outputs

- `source_inventory`: source type, owner, boundary, freshness, and access notes.
- `candidate_objects`: raw label, suggested business label, source evidence, confidence, and open questions.
- `boundary_gaps`: unknown scope or missing sources that block later stages.

## Quality bar

- Favor business objects such as `Machine`, `Alarm`, `Sensor`, `WorkOrder`, and `ProductionLine` over implementation-only artifacts such as `t_device` or `api_v2_status`.
- If a source looks purely technical, keep it as evidence until a later stage confirms whether it maps to a business object.
- Ask for missing source boundary only when it blocks the next step.
