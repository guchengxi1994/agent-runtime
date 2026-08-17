---
name: document-kb-status
description: 查看 workspace 文档知识库的切片总数、已完成和待处理切片、失败记录与知识节点数量。用于长文档抽取的断点恢复和覆盖率判断。
metadata:
  owner: runtime
  category: document-knowledge
  stage: status
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        document_id:
          type: string
          description: document-kb-ingest 返回的文档 id。
        pending_limit:
          type: integer
          description: 返回多少个待处理 segment id。
          default: 20
      required:
        - document_id
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Document KB Status

Use this skill at the start of a resumed document workflow. Do not infer completeness from the existence of a Markdown overview; only completed segments with stored evidence-backed nodes count as extracted coverage.
