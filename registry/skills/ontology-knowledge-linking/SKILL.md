---
name: ontology-knowledge-linking
description: Link industrial documents, SOPs, manuals, maintenance logs, CAD files, images, videos, alarm histories, and other knowledge artifacts to ontology objects, relations, and behaviors. Use when the task is to attach operational knowledge to a canonical ontology for retrieval, explanation, or runtime assistance.
metadata:
  owner: runtime
  category: industrial-ontology
  stage: knowledge-linking
  ontology_capability:
    capability_type: ontology-engineering-capability
    input_objects:
      - canonical ontology
      - documents
      - sops
      - manuals
      - maintenance records
      - cad
      - images
      - videos
      - alarm histories
    output_objects:
      - knowledge anchors
      - provenance links
      - retrieval hints
      - coverage gaps
    prerequisites:
      - ontology-normalization
    ai_capabilities:
      - llm-reasoning
      - retrieval
      - embeddings
    human_review: recommended
    rollback: true
    version: 0.1.0
    composable: true
  agent_runtime:
    capabilities:
      - ontology knowledge linking
      - industrial document linking
      - ontology evidence retrieval
---

# Ontology Knowledge Linking

Use this harness to connect industrial knowledge artifacts to ontology nodes.

## Working rules

1. Link documents, SOPs, manuals, maintenance records, CAD, images, and videos to the most specific ontology node supported by evidence.
2. Preserve provenance, document type, time range, and confidence for every link.
3. Separate authoritative knowledge sources from anecdotal or low-quality records.
4. Capture retrieval hints such as equipment model, line, alarm code, or revision number.
5. Leave ambiguous links unresolved rather than attaching weak evidence to the wrong object.

## Required outputs

- `knowledge_links`: ontology node, artifact type, artifact identifier, provenance, and confidence.
- `coverage_gaps`: objects or behaviors with missing supporting knowledge.
- `retrieval_hints`: search keys and filters that help runtime agents pull the right evidence later.

## Quality bar

- Manuals and SOPs should usually attach to machines, lines, or procedures rather than to broad enterprise nodes.
- Maintenance records should preserve asset identity and time context.
- Avoid treating every textual mention as a semantic link.
