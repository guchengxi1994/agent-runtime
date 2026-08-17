---
name: document-kb-read
description: 从 workspace 文档知识库读取少量稳定 Markdown segment，保留标题路径、页码和 segment_id，供模型进行证据化知识抽取或补充核查。不能读取全文。
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
        segment_ids:
          type: array
          description: 优先读取的稳定 segment id 列表，例如 seg_00001。
          items:
            type: string
        after_segment_id:
          type: string
          description: 未指定 segment_ids 时，从该 id 之后顺序读取。
        limit:
          type: integer
          description: 最多读取的 segment 数。长文抽取通常为 1-4。
          default: 2
      required:
        - document_id
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Document KB Read

Read only the next small batch needed for extraction or verification. Preserve `segment_id` in every evidence item submitted to `document-kb-upsert`.
