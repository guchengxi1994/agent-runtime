---
name: chart-spec-builder
description: 从结构化数据行构建前端友好的图表规格，适用于趋势、分布、矩阵和对比图。适用于报告或仪表板需要返回可供前端直接渲染的图表 JSON，而不是只给文字描述的场景。
metadata:
  owner: runtime
  category: report-analysis
  stage: visualization
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        chart_type:
          type: string
          description: 图表类型，例如 line、bar、stacked_bar、pie、heatmap。
        title:
          type: string
          description: 图表标题。
        subtitle:
          type: string
          description: 可选副标题。
        x_field:
          type: string
          description: 用作 x 轴或类别维度的字段名。
        y_fields:
          type: array
          description: 用作一个或多个序列的数值字段。
          items:
            type: string
        rows:
          type: array
          description: 用于构建图表的结构化数据行对象。
          items:
            type: object
            additionalProperties: true
        series_name_map:
          type: object
          description: 可选的字段名到显示名映射。
          additionalProperties: true
      required:
        - chart_type
        - title
        - rows
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# 图表规格生成

使用这个 executable skill 生成图表规格，而不是直接生成渲染后的图片。

优先输出兼容 ECharts 的 JSON，因为前端后续可以直接渲染。
