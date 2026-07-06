---
name: ontology-validation
description: Validate industrial ontology structure, naming, relations, mappings, behaviors, and knowledge links before release or runtime use. Use when the task is to detect duplicates, conflicts, invalid loops, broken mappings, missing units, unsafe behaviors, or other ontology quality issues.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: validation
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - canonical ontology
      - relations
      - mappings
      - behaviors
      - knowledge links
    output_objects:
      - validation findings
      - release recommendation
      - remediation queue
    prerequisites:
      - ontology-normalization
      - ontology-mapping
    ai_capabilities:
      - llm-reasoning
      - rules
      - deterministic-validation
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology validation
      - ontology quality checks
      - release gate
---

# Ontology Validation

Use this harness to block low-quality ontology changes before publish or runtime automation.

## Working rules

1. Run `ontology-structure-validator` on the working ontology or on the changed subset when full validation is not available.
2. Separate errors, warnings, and informational findings.
3. Check structure, naming, relation integrity, mapping completeness, unit consistency, and behavior safety notes.
4. Preserve every finding with remediation guidance.
5. Require human review for any release candidate with remaining errors or safety-sensitive behavior changes.

## Required outputs

- `errors`: release-blocking findings.
- `warnings`: review findings that may still allow a controlled release.
- `release_recommendation`: `pass`, `review`, or `block`.
- `remediation_queue`: next actions grouped by owner and stage.

## Quality bar

- Do not release self-looping hierarchy relations without explicit justification.
- Do not accept numeric engineering properties with silent or missing unit assumptions.
- Treat incomplete write mappings and unreviewed commands as high-severity findings.
