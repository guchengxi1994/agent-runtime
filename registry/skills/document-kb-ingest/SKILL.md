---
name: document-kb-ingest
description: 将 workspace 中已经转换完成的 Markdown 文档创建为可断点续跑的稳定切片语料库，并建立 SQLite FTS 索引。用于长文档知识库的第一步；不调用大模型抽取知识点。
metadata:
  owner: runtime
  category: document-knowledge
  stage: ingest
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        source_path:
          type: string
          description: 必填。相对于当前 workspace 根目录的 Markdown 路径，必须位于 document_knowledge/uploads/ 下，例如 document_knowledge/uploads/steel-handbook.md。
        document_id:
          type: string
          description: 可选的稳定文档 id。省略时根据 source_path 生成。重复调用同一 source hash 会安全复用已有索引。
        title:
          type: string
          description: 可选的显示标题。
        parser:
          type: string
          description: 转换器标识，例如 anydoc 或 mineru。
          default: unknown
        segment_target_chars:
          type: integer
          description: 单 segment 的目标字符数。按 Markdown 标题、表格、代码块和段落边界切分；默认 7000。
          default: 7000
        force_rebuild:
          type: boolean
          description: 仅当源 Markdown 已更新且需要重新建立切片时为 true。会清空此前的知识节点，必须获得用户明确确认。
          default: false
      required:
        - source_path
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 15000
      packages: []
---

# Document KB Ingest

Create stable source segments from a complete Markdown file. The source must be persisted before this skill runs; it is not a chat attachment parser.

Return values include `document_id`, source hash, segment count, and the first pending ids. After this succeeds, use `document-kb-read` and `document-kb-upsert` in small batches.

Never set `force_rebuild` merely to retry a failed extraction. Use it only after the parsed Markdown itself changes.
