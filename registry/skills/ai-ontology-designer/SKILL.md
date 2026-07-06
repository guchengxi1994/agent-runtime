---
name: ai-ontology-designer
description: Cross-stage design harness for AI-assisted industrial ontology modeling, governance, standards alignment, conflict detection, and change rationale. Use when the task requires ontology design recommendations, naming conflict resolution, industry-standard mapping, migration suggestions, or explanation of why a proposed ontology structure is sound.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: cross-cutting
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - source evidence
      - ontology drafts
      - standards references
      - change sets
    output_objects:
      - design recommendations
      - standards alignment notes
      - conflict explanations
      - migration suggestions
    prerequisites: []
    ai_capabilities:
      - llm-reasoning
      - standards-mapping
      - impact-analysis
      - explanation
    human_review: required
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ai ontology design
      - ontology governance assistant
      - standards alignment
---

# AI Ontology Designer

Use this harness as the cross-cutting design assistant for ontology engineering work.

## Working rules

1. Intervene when naming, hierarchy, behavior, mapping, or release decisions need design rationale.
2. Recommend standards alignment when ISA-95, AAS, OPC UA companion specifications, or similar models would reduce long-term entropy.
3. Explain tradeoffs explicitly: what is preserved, what changes, what downstream assets are affected, and why.
4. Prefer the simplest ontology that still captures the business semantics and runtime needs.
5. If current external standards or vendor docs matter, use `web-search` and `web-fetch` to gather primary sources before making claims.

## Required outputs

- `design_recommendations`: proposed model changes and rationale.
- `standards_alignment`: where the draft ontology matches or diverges from a reference model.
- `impact_notes`: affected mappings, behaviors, workflows, dashboards, or release plans.
- `review_questions`: the minimum human decisions needed to proceed safely.

## Quality bar

- Do not treat standards as mandatory if they distort the actual plant model.
- Justify merges and splits in business terms, not only in database terms.
- Keep recommendations version-aware so publish and evolution can consume them directly.
