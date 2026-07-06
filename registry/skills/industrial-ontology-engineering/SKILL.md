---
name: industrial-ontology-engineering
description: End-to-end harness for industrial ontology engineering across discovery, extraction, relation discovery, normalization, mapping, behavior discovery, knowledge linking, validation, publish, evolution, template reuse, and AI-assisted ontology governance. Use when the user wants to design, bootstrap, refactor, validate, version, or continuously evolve an industrial ontology instead of merely calling raw SQL, HTTP, PLC, MQTT, OPC UA, or document tools.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: orchestration
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
      - configs
      - existing ontologies
    output_objects:
      - ontology plan
      - stage routing
      - review gates
      - release artifacts
    prerequisites: []
    ai_capabilities:
      - llm-reasoning
      - rules
      - embeddings
      - standards-mapping
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - industrial ontology engineering
      - ontology pipeline orchestration
      - industrial knowledge graph design
      - ontology governance
---

# Industrial Ontology Engineering

Treat each skill as an ontology engineering capability inside a governed lifecycle, not as a disconnected tool.

## Routing order

1. If the user needs a starting model, activate `ontology-marketplace` and call `ontology-template-marketplace`.
2. Use `ontology-discovery` to enumerate candidate objects and source boundaries.
3. Use `ontology-extraction` to pull properties, enums, units, datatypes, keys, and constraints.
4. Use `ontology-relation-discovery` to convert foreign keys, tag paths, and document evidence into typed business relations.
5. Use `ontology-normalization` to merge aliases, canonicalize names, and standardize units.
6. Use `ontology-mapping` to bind canonical ontology fields to SQL tables, API paths, PLC registers, MQTT topics, or OPC UA nodes.
7. Use `ontology-behavior-discovery` to convert commands, procedures, workflows, or write paths into callable object behaviors.
8. Use `ontology-knowledge-linking` to attach SOPs, manuals, maintenance logs, CAD, images, and videos to ontology nodes.
9. When extracted objects, mappings, SQL schema, or tag CSV must become reusable runtime state, call `ontology-registry-upsert` to persist them into the current workspace ontology registry.
10. Before querying or diagnosing, call `ontology-runtime-resolve` to turn business references such as a production line, machine, or metric into concrete entity refs, source bindings, and related runtime context.
11. Use `timeseries-query-sql` for read-only telemetry or event slices after runtime resolution.
12. Use `energy-anomaly-diagnose` when the user asks why a line, machine, or process segment shows abnormal energy behavior during a given window.
13. Use `ontology-validation` and `ontology-structure-validator` before any release or runtime usage.
14. Use `ontology-publish` for versioning, impact analysis, migration planning, and release readiness.
15. Use `ontology-evolution` for drift detection, rescans, and update recommendations.
16. Use `ai-ontology-designer` whenever the task requires design rationale, standards alignment, conflict resolution, or change recommendations across stages.

## Chat-only mode

If the client has only a text conversation entry and no file upload or parser pipeline, continue in chat-only mode instead of blocking.

1. Start from an industry template when possible.
2. Ask only for the next source snippet that unlocks progress.
3. Accept pasted content inside markdown code fences or plain text blocks.
4. Process one source family at a time: database schema, API fragment, PLC or OPC UA tag list, MQTT topics, or document excerpt.
5. Produce an intermediate ontology draft after each snippet instead of waiting for a full corpus.

Preferred pasted input formats:

- SQL DDL or a few representative table definitions.
- OpenAPI path fragments or example request and response payloads.
- PLC, OPC UA, or MQTT tag lists in CSV-like rows.
- SOP, manual, or alarm text excerpts with object names preserved.
- Existing ontology fragments in JSON or YAML.

When the user has not provided source text yet, ask for one of these four smallest useful inputs:

- one database table or view definition
- one API path group
- one machine tag list
- one short document excerpt

## Core operating rules

- Model business objects first, implementation artifacts second.
- Keep candidate objects, canonical objects, and runtime mappings as separate layers.
- Never normalize names before preserving raw aliases and source evidence.
- Do not publish AI-generated relations, behaviors, or mappings without a review gate.
- Prefer reusable industry templates over greenfield modeling when a close domain match exists.
- Keep every output versionable, diffable, and reversible.

## Minimum deliverables per ontology increment

- Business scope and source boundary.
- Candidate and canonical object list.
- Property model with datatype, unit, enum, and constraint notes.
- Relation model with evidence.
- Runtime mapping set.
- Behavior catalog.
- Knowledge anchors.
- Workspace-persistent runtime registry state when the ontology must be reused across turns or conversations.
- Validation findings and release recommendation.
- Change log and next evolution triggers.

Read `references/pipeline-contract.md` when the user needs the shared capability contract, naming rules, or release artifact checklist.
