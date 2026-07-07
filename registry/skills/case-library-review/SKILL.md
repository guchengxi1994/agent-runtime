---
name: case-library-review
description: 通过紧凑索引和按需读取案例文件来审阅本地精选案例库的 harness skill。适用于运行时只需要少量代表性本地案例，尤其是在数据库案例检索链路暂不可用，或需要补充说明性案例时使用。
metadata:
  owner: runtime
  category: report-analysis
  stage: evidence
  agent_runtime:
    capabilities:
      - 案例证据检索
      - 本地文档路由
      - 紧凑索引读取
---

# 本地案例库审阅

当报告写作需要从受控案例库中提取精选本地案例证据时，使用这个 harness。

如果案例主库是 PostgreSQL，优先使用 `pg-case-search`。

## 检索规则

不要把整个案例库一次性载入上下文。

## 必要流程

1. 先阅读 `references/case-index.json`。
2. 先根据索引字段筛选最相关案例，优先维度包括：
   - topic
   - risk_type
   - industry
   - year
   - amount_band
3. 只读取真正入选的 `references/cases/` 下案例文件。
4. 只引用或概述当前段落真正需要的部分，不要把案例全文复述出来。

## 案例库组织建议

- 保持索引紧凑。
- 每个案例文件都保持为短小、证据导向的摘要。
- 不要在索引里复制整篇案例正文。

## 写报告时的使用方式

- 通常只选 1-3 个代表性案例。
- 优先选择能直接支撑核心风险判断的案例。
- 如果多个案例重复同一模式，概括模式即可，不要逐个罗列。
