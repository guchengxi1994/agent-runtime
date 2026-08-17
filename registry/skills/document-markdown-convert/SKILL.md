---
name: document-markdown-convert
description: 将 workspace 中已持久化的 PDF、Office、EPUB、RTF、CSV、HTML、文本或 Markdown 原文件转换为持久化 Markdown。用于长文档知识库的入口；支持本地 firecrawl-anydoc 与可选 MinerU OCR，转换结果不会返回到对话上下文。
metadata:
  owner: runtime
  category: document-knowledge
  stage: conversion
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        source_path:
          type: string
          description: 必填。相对于当前 workspace 根目录的原文件路径，必须位于 document_knowledge/uploads_raw/ 下，例如 document_knowledge/uploads_raw/steel-handbook.pdf。
        document_id:
          type: string
          description: 可选的稳定文档 id。省略时由原文件名生成。相同 id 的原文件变更需要 force=true 或使用新 id。
        converter:
          type: string
          enum: [auto, anydoc, mineru]
          description: auto 对 Markdown/文本直接规范化，对其他受支持格式使用 anydoc；mineru 用于扫描件、公式或复杂表格。
          default: auto
        mineru_url:
          type: string
          description: 可选 MinerU 服务地址。未提供时读取该 skill 的 MINERU_PROXY_URL，例如 http://mineru:8011。
        language:
          type: string
          description: MinerU 识别语言，例如 ch 或 en。
          default: ch
        force:
          type: boolean
          description: 原文件内容变化后重新转换同一 document_id 时设为 true。不会修改 uploads_raw 中的原文件。
          default: false
      required:
        - source_path
      additionalProperties: false
    execution_policy:
      timeout_ms: 600000
      idle_timeout_ms: 120000
      packages:
        - firecrawl-anydoc==0.1.9
        - requests==2.32.3
---

# Document Markdown Convert

The raw document must already be saved in `document_knowledge/uploads_raw/`. This skill creates a Markdown derivative in `document_knowledge/uploads/` and records conversion metadata under the document id. It never returns the document body.

Use `auto` for text-native files. Use `mineru` deliberately for scanned PDFs, pages dominated by formulas or complex tables, or after `anydoc` reports no meaningful content. MinerU requires `MINERU_OCR_TOKEN`; configure it from this skill package's `.env.example` on the sandbox host. MinerU can be slow; do not retry it merely because the request is still running.

After success, call `document-kb-ingest` with the returned `markdown_path` and `document_id`. Do not put the Markdown in chat or use `read_artifact` to read it.
