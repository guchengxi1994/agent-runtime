---
name: document-kb-query
description: 在 workspace 文档知识库中按全文检索已提取的知识节点及其原文证据；节点不足时返回可按需读取的源 segment 候选，而不是全文。用于工具书、专著、论文和技术资料的可追溯问答。
metadata:
  owner: runtime
  category: document-knowledge
  stage: query
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        document_id:
          type: string
          description: document-kb-ingest 返回的文档 id。
        query:
          type: string
          description: 要检索的概念、方法、结论或问题关键词。
        limit:
          type: integer
          description: 最多返回的知识节点数。
          default: 8
        include_source_candidates:
          type: boolean
          description: 无节点命中或需要补充原文时，是否返回可由 document-kb-read 读取的 segment 候选。
          default: true
      required:
        - document_id
        - query
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Document KB Query

Search extracted nodes first. Treat returned evidence as the citation boundary. If `source_candidates` are returned, use `document-kb-read` on only the selected ids, then add new evidence-backed nodes with `document-kb-upsert`; do not answer from an unprocessed document as though extraction were complete.
