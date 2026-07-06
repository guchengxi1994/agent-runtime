---
name: ontology-evolution
description: Detect industrial ontology drift, compare new source changes against released versions, and generate update recommendations for continuous ontology evolution. Use when the task is to rescan sources, detect added fields or behaviors, classify drift, and propose controlled ontology updates over time.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: evolution
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - released ontology
      - new source scans
      - api diffs
      - schema diffs
      - document revisions
    output_objects:
      - drift report
      - change suggestions
      - confidence notes
      - publish candidates
    prerequisites:
      - ontology-publish
    ai_capabilities:
      - llm-reasoning
      - rules
      - diff-analysis
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology evolution
      - drift detection
      - ontology update recommendation
---

# Ontology Evolution

Use this harness to keep industrial ontologies aligned with changing plants, systems, and documents.

## Working rules

1. Re-scan changed schemas, APIs, documents, tags, and procedures on a controlled cadence.
2. Compare new evidence against the latest released ontology, not against an old draft.
3. Classify detected changes as additive, breaking, or ambiguous.
4. Turn accepted drift into a new publish candidate instead of editing the released version in place.
5. Escalate low-confidence or high-impact changes for review.

## Required outputs

- `drift_report`: new fields, removed fields, changed semantics, new behaviors, or missing relations.
- `change_suggestions`: recommended ontology updates with confidence and impact.
- `review_queue`: ambiguous or breaking changes needing human approval.

## Quality bar

- A new database column or API action is only a suggestion until validated.
- Track source change time, detection time, and ontology impact separately.
- Preserve the lineage from detected drift to published change set.
