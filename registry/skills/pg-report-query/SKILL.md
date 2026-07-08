---
name: pg-report-query
description: 面向报告场景规划或执行 PostgreSQL 查询，支持聚合指标、趋势序列、分组统计和案件结构交叉分析。适用于报告需要从 PG 数据源获取结构化量化证据，尤其是在已经通过 pg-table-profile 确认真实列名、字段类型和低基数字段取值之后。
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
        top_n:
          type: integer
          description: 只保留前 N 个结果，常用于排行问题。
        order_by:
          type: string
          description: 排序字段，可用维度别名或指标别名，例如 case_count、industry、month。
        order_direction:
          type: string
          description: 排序方向，asc 或 desc。
        include_share:
          type: boolean
          description: 是否附带占比指标，适用于构成分析。
        include_chart:
          type: boolean
          description: 是否在结果中附带前端可直接渲染的图表规格。
        chart_type:
          type: string
          description: 可选图表类型提示，例如 line、bar、stacked_bar、pie。
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

当前这批天宁区企业涉法涉诉案件数据已经预先限定在天宁区范围内。

- 当问题里出现 `天宁区`、`常州市天宁区`、`常州` 这类词时，默认应理解为数据范围背景，而不是再额外添加 `region` 过滤。
- 当前数据里的 `region` 更适合解释为街道、板块、园区或属地单元，用来做区内聚合分析。

使用方式：

- 如果不确定真实列名、字段类型或枚举值，先调用 `pg-table-profile`，确认可用字段后再写 SQL。
- 如果 `execute=false`，返回查询方案和假设。
- 如果 `execute=true` 且 PostgreSQL 可用，会直接执行查询。
- 如果数据库里还没有案件数据，不要伪造统计结果，应提示先调用 `pg-risk-dataset-sync`。

当前内置的通用维度和指标面向企业涉法涉诉案件数据集，适合：

- 月度趋势
- 地区、行业、所有权性质、企业规模分布
- 司法案件与行政处罚的来源拆分
- 案件大类、案件子类和事项类型的结构分析

当前版本除了显式参数，还会从中文问题里自动推断：

- 时间范围，例如 `2025年`、`2025年以来`
- 来源类型，例如 `行政处罚`、`司法案件`
- 常见分组维度，例如 `行业`、`区域`、`月份`、`案件类型`
- 排行语义，例如 `前3`、`最多`
- 常见图表需求，例如 `趋势图`、`柱状图`、`饼图`

注意：

- 不要凭空假设 `event_category`、`case_type` 这类列一定存在。
- 如果之前 SQL 因为列不存在而失败，应先用 `pg-table-profile` 查看真实表结构和低基数字段取值，再手写 `sql` 参数重新执行。
