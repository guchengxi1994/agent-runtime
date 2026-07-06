---
name: ontology-behavior-discovery
description: Discover industrial object behaviors from APIs, SQL mutations, workflow steps, command topics, PLC write paths, and operational procedures. Use when the task is to turn actions like POST /device/start or a reset procedure into ontology behaviors such as Machine.start or Machine.reset.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: behavior-discovery
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - canonical objects
      - api specs
      - workflows
      - sql procedures
      - command topics
      - plc write paths
      - procedures
    output_objects:
      - behavior catalog
      - preconditions
      - safety notes
      - compensation notes
    prerequisites:
      - ontology-normalization
      - ontology-mapping
    ai_capabilities:
      - llm-reasoning
      - rules
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology behavior discovery
      - action modeling
      - industrial command extraction
---

# Ontology Behavior Discovery

Use this harness to model callable industrial behaviors instead of leaving actions buried inside raw endpoints or procedures.

## Working rules

1. Convert commands and state-changing operations into object-centric behaviors.
2. Distinguish read-only queries from write or state-change behaviors.
3. Capture source action, preconditions, approval needs, idempotency, and compensation notes.
4. Keep safety-sensitive actions explicit.
5. Preserve the source verb even when the canonical behavior name changes.

## Required outputs

- `behaviors`: object, behavior name, source action, preconditions, safety notes, and compensation hints.
- `behavior_gaps`: commands lacking ownership, approval policy, or clear target objects.
- `approval_queue`: behaviors that must be reviewed before runtime automation.

## Quality bar

- `Machine.start`, `Machine.stop`, `Machine.reset`, and similar behaviors must link to concrete execution paths.
- Avoid modeling every endpoint as a behavior; only promote operations with clear business intent.
- Manual or high-risk procedures must carry approval requirements forward to runtime.
