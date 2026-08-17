---
name: document-kb-upsert
description: 将模型从一小批文档 segment 提取出的知识节点和原文证据写入 workspace 文档知识库，并仅在写入成功后更新抽取进度。用于长文档的增量、可恢复知识抽取；不接受无 segment 证据的事实。
metadata:
  owner: runtime
  category: document-knowledge
  stage: extract
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        document_id:
          type: string
          description: document-kb-ingest 返回的文档 id。
        processed_segment_ids:
          type: array
          description: 本次已完成阅读和判断的 segment id。即使没有值得保留的节点，也应提交该 segment 以推进覆盖率。
          items:
            type: string
        nodes:
          type: array
          description: 本次抽取的证据化知识节点。每项必须包含 type、title、statement 和 evidence；evidence 里的 segment_id 必须属于当前文档。
          items:
            type: object
            additionalProperties: true
      required:
        - document_id
        - processed_segment_ids
        - nodes
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Document KB Upsert

Only submit nodes that the current source segments support. Each node has `type`, `title`, `statement`, optional `scope`, `aliases`, `relations`, `confidence`, and an `evidence` list. Each evidence item needs `segment_id`, plus a short exact `quote` or `locator`; quotes are capped at 1,000 characters.

Use `confidence: explicit` for direct statements and `inferred` only for a clearly bounded synthesis. Submit `nodes: []` when a reviewed segment has no reusable knowledge; that is still a successful extraction pass.
