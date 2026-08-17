---
name: document-knowledge
description: 将已经转换为 Markdown 的工具书、专著、论文或技术文档拆解成带原文证据、可断点续跑、可查询的 workspace 知识库。适用于长文档知识点提取、概念查询、方法复用、论文结论回查和文献问答；不用于一次性全文摘要。
metadata:
  owner: runtime
  category: document-knowledge
  stage: orchestration
  agent_runtime:
    capabilities:
      - long document knowledge extraction
      - evidence-backed knowledge base
      - resumable document processing
      - document knowledge query
---

# Document Knowledge

Treat a converted Markdown document as a persistent source corpus, not as chat context.

## Input contract

The original file must already exist under the current workspace at:

`document_knowledge/uploads_raw/{file}`

Call `document-markdown-convert` before ingestion. It creates the canonical Markdown derivative at:

`document_knowledge/uploads/{file}.md`

The dedicated document upload endpoint is responsible for persisting raw input. Do not paste a long document into a tool argument or into chat. Preserve source anchors such as `<!-- source-anchor: page=47 -->` when the converter can provide them.

## Build workflow

1. Call `document-markdown-convert` once with the workspace-relative raw source path.
2. Call `document-kb-ingest` once with the returned workspace-relative Markdown path and `document_id`.
3. Call `document-kb-status` and use its pending segment ids as the work queue.
4. Call `document-kb-read` for a small batch. Keep each model turn below its context budget; prefer 1-4 segments, never request the whole document.
5. Extract only source-grounded knowledge nodes from that batch and call `document-kb-upsert`. Every hard fact needs an evidence item with its `segment_id` and a short exact quote or locator.
6. Repeat over later user turns. Resume from `document-kb-status`; never reprocess segments already listed as completed unless the user explicitly requests revision.
7. Use `document-kb-query` for questions after, or during, extraction. Clearly state when coverage is partial.

## Node types

Use the smallest fitting type: `definition`, `claim`, `method`, `procedure`, `formula`, `result`, `limitation`, `example`, `warning`, `citation`, `argument`, or `relation`.

Keep source facts separate from interpretation:

- `confidence: explicit` only when the source directly states it.
- `confidence: inferred` when synthesizing; say what supports the inference.
- Preserve contradictory claims as separate nodes with their own scopes and evidence.
- Do not invent a concept, value, page, citation, or applicability condition.

## Query workflow

For a question, call `document-kb-query` first. Answer from returned nodes and their evidence. If coverage is partial or no node is found, use `document-kb-read` on returned source candidates, then either add evidence-backed nodes or explain that the corpus does not establish an answer.

## Long-document rules

- Markdown is the canonical parsed source; JSONL and SQLite are the knowledge/index layers.
- Do not ask the user to resend a document merely because a conversation ended.
- Do not use `read_artifact` to retrieve document source. Use `document-kb-read`, which is scoped to stable segments.
- A failure in one segment is local. Leave it pending and proceed with independent segments.
