---
name: pg-table-profile
description: 探查 PostgreSQL 表结构、字段类型、可分组维度、低基数字段枚举值和代表性取值。适用于在写 SQL 之前先确认真实列名和字段取值分布，避免大模型凭空假设不存在的字段或错误的枚举值。
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
        table_name:
          type: string
          description: 需要探查的表名，默认 enterprise_risk_events。
        table_schema:
          type: string
          description: 可选 schema 名称，默认优先 public，再回退 current_schema()。
        focus_columns:
          type: array
          description: 需要重点展开取值概览的字段名列表。
          items:
            type: string
        max_enum_values:
          type: integer
          description: 当字段低基数时，最多返回多少个枚举值预览。
          default: 12
        max_top_values:
          type: integer
          description: 当字段高基数时，最多返回多少个高频取值预览。
          default: 8
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 15000
      packages:
        - psycopg[binary]==3.2.9
---

# PostgreSQL 表结构与枚举探查

在编写或修正 SQL 之前，先用这个 skill 看清楚真实表结构。

适用场景：

- 需要确认真实列名
- 需要确认哪些字段适合做维度分组
- 需要知道 `event_source`、`region`、`industry`、`ownership_nature` 等字段有哪些常见取值
- 之前 SQL 因为假设了不存在的列名而失败
- 需要给后续查询或报告写作提供可靠的 schema 上下文

输出重点：

- 表行数和字段数
- 每个字段的数据类型、是否可空
- 可作为维度的字段候选
- 低基数字段的枚举值预览
- 高基数字段的高频取值预览
- 一段给模型直接消费的 schema 摘要

使用建议：

1. 先调用这个 skill。
2. 看清真实列名和字段取值。
3. 再手写 SQL，或把确认后的列名传给 `pg-report-query`。
4. 如果 `pg-report-query` 报 `schema_mismatch`，回到这里重新确认。

环境变量示例见同目录 `.env.example`。
