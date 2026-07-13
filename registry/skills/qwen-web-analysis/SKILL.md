---
name: qwen-web-analysis
description: Use a DashScope-compatible Qwen or similar model with native web search enabled to explain why an observed pattern exists, what mechanisms drive it, and what it implies for governance, service delivery, or resource allocation. Use when the user asks interpretive “why / what does this mean” questions after local data has already shown concentration, pressure, imbalance, or structural differences.
metadata:
  owner: runtime
  category: research-analysis
  agent_runtime:
    executable: true
    entrypoint: skill.py
    capabilities:
      - 联网解释型分析
      - 结构成因研判
      - 治理资源配置分析
      - 中文正式分析写作
    parameters_schema:
      type: object
      properties:
        mode:
          type: string
          description: 联网分析模式。`explain` 用于解释成因和治理含义；`recommend` 用于检索外部经验并生成对策建议。
          enum:
            - explain
            - recommend
          default: explain
        question:
          type: string
          description: 核心问题。`explain` 模式下填写解释性问题；`recommend` 模式下填写需要回应的治理焦点。
        evidence_summary:
          type: string
          description: 可选。本地数据、图表结论、统计结果或案件摘要，用于约束联网分析口径。
        recommendation_focus:
          type: string
          description: 可选。仅推荐在 `recommend` 模式下提供，说明要针对哪一类问题搜集可借鉴经验，例如高频涉诉行业治理、重点街道涉企纠纷化解、行政处罚高发事项整治。
        institution_perspective:
          type: string
          description: 可选。机构口径，默认 procuratorate。用于在 `recommend` 模式下约束建议表达视角。
          enum:
            - procuratorate
            - generic
          default: procuratorate
        analysis_scope:
          type: string
          description: 可选。分析对象范围，例如天宁区企业涉法涉诉案件、某行业、某板块、某类主体。
        region:
          type: string
          description: 可选。地区口径提示。
        time_range:
          type: string
          description: 可选。时间范围提示。
        answer_language:
          type: string
          description: 回答语言，默认 zh-CN。
          default: zh-CN
      required:
        - mode
        - question
      additionalProperties: false
    execution_policy:
      timeout_ms: 150000
      idle_timeout_ms: 90000
      packages:
        - requests==2.32.3
    required_secrets:
      OPENAI_API_KEY: env:OPENAI_API_KEY
      AGENT_RUNTIME_MODEL: env:AGENT_RUNTIME_MODEL
      OPENAI_BASE_URL: env:OPENAI_BASE_URL
---

# Qwen Web Analysis

把这个 skill 视为“联网分析增强器”，不是普通搜索工具。

它依赖 DashScope 兼容 OpenAI 的模型接口，并直接启用模型原生联网搜索能力，不再额外走 Quark 或其他外部检索链。

执行时使用 OpenAI 兼容的 `chat/completions` 流式响应，设置 `enable_search=true`、`search_options.forced_search=true` 和 `stream=true`。skill 会逐块合并正文并向 sandbox 发送轻量进度心跳，避免联网搜索阶段因长时间没有进程输出而被误判为 idle timeout。

## 适用场景

优先用于这类问题：

- 为什么某类行业更集中
- 为什么某个板块案件压力更高
- 为什么某类主体更容易成为高频涉诉对象
- 当前结构对治理资源配置意味着什么
- 针对当前高发问题，其他地区有哪些可借鉴治理做法
- 针对当前高频涉诉行业或板块，下一步可落地的对策建议是什么

它特别适合接在本地结构化数据分析之后使用。也就是说，先有本地事实，再用联网信息补“原因解释、外部对照、治理含义”这一层。

完整报告里通常只应在证据已经基本锁定后调用 1 次；确有必要时最多 2 次。不要把它当成逐段补资料的常规搜索器。
如果它超时、失败或未返回有效来源，不应阻塞正式成稿；直接回退到本地证据写作即可。

## 输入建议

- `mode` 必填：
  - `explain`：解释为什么会这样、意味着什么
  - `recommend`：检索外部可借鉴经验，并形成对策建议
- `question` 必填：
  - `explain` 模式下，尽量直接写成“为什么 / 这说明什么 / 对治理意味着什么”
  - `recommend` 模式下，尽量直接写成“针对什么问题，外部有哪些有效做法 / 可借鉴经验 / 建议动作”
- 一次只提交一个最重要的问题，不要把多个并列搜索条件塞进同一次调用。
- `evidence_summary` 强烈建议提供，尤其是已经有本地统计结果时：
  - 行业前五
  - 板块分布
  - 主体结构
  - 典型案件
- `evidence_summary` 可以写得稍详细一些，让模型把篇幅花在解释上，而不是花在重新摸清背景上。
- 但也不要把整批原始明细直接塞进去；优先压缩成关键数字、头部结构、异常点和案例摘要。
- `recommendation_focus` 建议在 `recommend` 模式下补充，明确要搜哪类做法，例如：
  - 商贸服务业高频涉诉治理
  - 某街道涉企纠纷多发化解机制
  - 行政处罚高发事项源头预防
- `institution_perspective` 在当前报告场景下默认使用 `procuratorate`，表示建议段要体现检察履职视角，而不是普通行业咨询口径。
- `analysis_scope`、`region`、`time_range` 只用于收紧口径，不必面面俱到。

## 工作方式

1. 直接调用 DashScope 兼容接口。
2. 在请求体中启用模型原生联网搜索。
3. 把“本地证据 + 联网搜索结果”一次性综合为中文分析结论。
4. 不再额外串联 Quark、`web-search`、`web-fetch` 之类的外部检索 skill。

## 输出要求

输出应尽量包含：

- `explain` 模式：
  - 核心判断
  - 形成机制
  - 对治理、服务供给或资源倾斜的启示
- `recommend` 模式：
  - 本地发现对应的核心问题
  - 可借鉴经验
  - 与本地问题相对应的对策建议
  - 如为 `procuratorate` 口径，建议尽量区分“党委政府主责”和“检察履职协同”
- 不确定性与边界
- 参考来源

## 使用边界

- 不要把它当作事实库替代本地数据库。
- 如果本地数据已经足够回答“有多少、排第几、占比多少”，优先用本地查询 skill，不要滥用联网分析。
- 不要为每个章节、每张图、每个局部现象都单独调用一次。优先挑 1-2 个最值得解释的结构性问题。
- 对策建议或下一步安排如需增强，优先只调用 1 次 `recommend` 模式，把本地最重要的 1-2 个治理焦点合并成一个建议任务。
- 如果问题高度依赖本地事实，而 `evidence_summary` 又为空，结论应更谨慎。
- 如果你的网关虽然兼容 OpenAI，但不支持 DashScope 的原生联网参数，这个 skill 会失败；这时需要把 `OPENAI_BASE_URL` 指到支持该能力的兼容端点。

环境变量示例见同目录 `.env.example`。
