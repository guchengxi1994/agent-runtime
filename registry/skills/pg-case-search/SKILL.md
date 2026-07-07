---
name: pg-case-search
description: 面向报告写作规划或执行 PostgreSQL 案例检索，支持标签过滤、模糊关键词检索、代表性案例抽样和证据候选清单生成。适用于运行时应优先在 PostgreSQL 案例库中检索，而不是把大量本地案例文件直接载入上下文的场景。
metadata:
  owner: runtime
  category: report-analysis
  stage: evidence
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        query:
          type: string
          description: 本次案例检索的自然语言目标或关键词查询。
        tags:
          type: array
          description: 可选案例标签，例如 illegal_subcontracting、leaseback、guarantee、restructuring。
          items:
            type: string
        filters:
          type: object
          description: 可选结构化过滤条件，例如 region、year、industry、amount_band、risk_level。
          additionalProperties: true
        limit:
          type: integer
          description: 候选案例清单最多返回多少条。
          default: 10
        sort_by:
          type: string
          description: 排序偏好，例如 relevance、amount_desc、year_desc、risk_desc。
        include_sql:
          type: boolean
          description: 返回结果中是否包含生成的 SQL 文本。
          default: true
        execute:
          type: boolean
          description: 为 true 时，如果后续配置了 DSN，则尝试对 PostgreSQL 执行。
          default: false
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 15000
      packages: []
---

# PostgreSQL 案例检索

当报告需要从较大案例库中提取代表性案例时，把这个 executable skill 作为默认案例检索层。

当前第一阶段的现实约束：

- PostgreSQL schema 可能仍在演进
- 线上 DSN 可能暂时不存在
- 即使无法直接执行，结果也应该仍然能作为可用的检索规划

如果暂时无法在线执行，至少返回：

- 检索 SQL 方案
- schema 假设
- 建议的索引或标签字段
- 可供规划器在报告流程中引用的候选筛选标准
