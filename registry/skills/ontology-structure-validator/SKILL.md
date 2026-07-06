---
name: ontology-structure-validator
description: Deterministically validate industrial ontology fragments for duplicate objects, duplicate properties, broken relations, invalid loops, missing enum definitions, incomplete mappings, and unit gaps. Use when the task is to perform a structural quality gate on ontology drafts before publish or runtime use.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: validation
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - ontology fragments
      - relation drafts
      - mappings
      - behaviors
    output_objects:
      - structural findings
      - release recommendation
    prerequisites: []
    ai_capabilities:
      - deterministic-validation
      - rules
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        ontology:
          type: object
          description: Ontology fragment containing objects and optional relations or mappings.
          properties:
            objects:
              type: array
              items:
                type: object
                properties:
                  name:
                    type: string
                  aliases:
                    type: array
                    items:
                      type: string
                  properties:
                    type: array
                    items:
                      type: object
                      properties:
                        name:
                          type: string
                        type:
                          type: string
                        unit:
                          type: string
                        enum_values:
                          type: array
                          items:
                            type: string
                      additionalProperties: true
                  behaviors:
                    type: array
                    items:
                      type: object
                      properties:
                        name:
                          type: string
                      additionalProperties: true
                required:
                  - name
                additionalProperties: true
            relations:
              type: array
              items:
                type: object
                properties:
                  source:
                    type: string
                  predicate:
                    type: string
                  target:
                    type: string
                required:
                  - source
                  - predicate
                  - target
                additionalProperties: true
            mappings:
              type: array
              items:
                type: object
                properties:
                  object:
                    type: string
                  property:
                    type: string
                  source_kind:
                    type: string
                  source_path:
                    type: string
                required:
                  - object
                  - property
                  - source_kind
                  - source_path
                additionalProperties: true
          required:
            - objects
          additionalProperties: true
        strict:
          type: boolean
          description: When true, promote some warnings such as missing units on engineering numbers into higher-severity findings.
          default: false
      required:
        - ontology
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Ontology Structure Validator

Use this executable skill as a deterministic quality gate for ontology drafts.

It validates structure only. It does not approve business semantics, safety policy, or release readiness on its own. Pair it with `ontology-validation` and `ontology-publish`.
