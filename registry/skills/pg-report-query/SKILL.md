---
name: pg-report-query
description: 面向报告场景规划或执行 PostgreSQL 查询，支持聚合指标、趋势序列、分组统计和风险交叉分析。适用于报告需要从未来的 PG 数据源获取结构化量化证据，即使当前 schema 还没有完全定型。
metadata:
  owner: runtime
  category: report-analysis
  stage: data
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        report_question:
          type: string
          description: 本次查询希望回答的业务问题。
        metrics:
          type: array
          description: 需要获取的指标名或语义指标。
          items:
            type: string
        dimensions:
          type: array
          description: 分组维度，例如 month、industry、region、risk_type。
          items:
            type: string
        filters:
          type: object
          description: 可选过滤条件对象。
          additionalProperties: true
        table_hints:
          type: array
          description: 可选候选来源表提示。
          items:
            type: string
        sql:
          type: string
          description: 如果已经明确 SQL，可直接传入原始 SQL。
        execute:
          type: boolean
          description: 为 true 时，如果后续配置了 DSN，则尝试执行 PostgreSQL 查询。
          default: false
        limit:
          type: integer
          description: 预览执行时的最大返回行数。
          default: 200
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 15000
      packages:
        - psycopg[binary]==3.2.9
---

# PostgreSQL 报表查询

把这个 executable skill 作为报告写作中的量化数据层。

环境变量示例见同目录 `.env.example`。

当前默认对接的事实表是 `enterprise_risk_events`。

使用方式：

- 如果 `execute=false`，返回查询方案和假设。
- 如果 `execute=true` 且 PostgreSQL 可用，会直接执行查询。
- 如果数据库里还没有风险数据，不要伪造统计结果，应提示先调用 `pg-risk-dataset-sync`。

当前内置的通用维度和指标面向企业风险数据集，适合：

- 月度趋势
- 地区、行业、所有权性质、企业规模分布
- 司法案件与行政处罚的来源拆分
- 案件类型和风险类型的结构分析
