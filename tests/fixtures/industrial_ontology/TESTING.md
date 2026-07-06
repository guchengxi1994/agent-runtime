# 工业本体前端调试说明

这份文档面向的是**通过当前前端聊天页面做手工调试**，不是 `pytest` 自动化测试。

当前前端入口：

- 打开 `http://127.0.0.1:8010/`
- 使用单页聊天界面
- 手动设置固定的 `Workspace`
- 上传测试数据文件
- 按阶段逐步发送调试指令

## 1. 当前前端能做什么，有哪些限制

当前前端已经够你验证这条新的 workspace 工业本体链路，但有几个限制要先知道：

1. 当前页面**没有 agent 选择器**。
   前端请求默认走 `agent_id=default`。
   目前实践上仍然能工作，因为默认 runtime 仍然能看到完整的 skill catalog。

2. 当前页面**没有 skill 显式选择器**。
   所以到底会不会激活 harness、会不会调用 executable skill，还是由模型自己决定。

3. 当前页面**没有 artifact 浏览器**。
   你仍然可以在本地目录直接检查：

   `artifacts/workspaces/<workspace_id>/`

4. 当前页面**没有结构化分步向导**。
   所以前端手测时，最好把流程拆成几个明确的阶段，一轮只做一件事。

因为有这些限制，为了让手测更稳定，建议你：

- 始终使用同一个固定 `Workspace`
- 一轮只测一个阶段
- 在 prompt 里明确点名相关 harness 或 executable skill
- 一轮只上传当前阶段真正需要的文件

## 2. 前端调试时要用到的文件

使用本目录下这些测试文件：

- `existing_ontology_fragment.json`
- `manufacturing_mes_schema.sql`
- `line3_tags.csv`
- `line3_power_timeseries.csv`
- `line3_alarm_events.csv`
- `maintenance_sop_excerpt.md`

可选文件：

- `device_service_openapi.yaml`

## 3. 推荐的前端调试流程

先固定一个 workspace，例如：

`steel_line3_debug`

下面所有步骤都不要改这个 workspace。

### Step A：先建立 workspace 级工业本体运行态

上传文件：

- `existing_ontology_fragment.json`
- `manufacturing_mes_schema.sql`
- `line3_tags.csv`

发送下面这段 prompt：

```text
这是一个工业本体运行态构建任务，不是普通总结。
请先读取并遵循 industrial-ontology-engineering harness。
然后基于附件内容，为当前 workspace 建立可复用的工业本体运行态。

要求：
1. 抽取对象、属性、关系、映射和实例
2. 必要时结合 schema_sql 和 tag_csv 做补全推断
3. 调用 ontology-registry-upsert 持久化到当前 workspace
4. 回复里说明实际调用了哪些 skill，以及当前 workspace 是否已经具备后续查询能力
```

### Step A 的预期结果

在前端的运行日志里，理想情况下应看到：

- `activate_skill`，目标是 `industrial-ontology-engineering`
- `ontology-registry-upsert`

你要验证的点：

- assistant 明确说运行态已经持久化
- 本地生成了文件：

  `artifacts/workspaces/steel_line3_debug/ontology_runtime.db`

如果模型只是做了文字总结，**没有**真的调用 `ontology-registry-upsert`，说明 prompt 还不够硬，或者模型漂了。
这时可以补一轮更强的 prompt：

```text
不要只做说明，请实际调用 ontology-registry-upsert，把附件内容写入当前 workspace。
```

## 4. Step B：把样例时序和告警数据写进去

上传文件：

- `line3_power_timeseries.csv`
- `line3_alarm_events.csv`

推荐 prompt：

```text
继续使用当前 workspace。
请把附件中的样例时序和告警数据写入已经建立好的工业本体运行态。

要求：
1. 不要重新做整套本体设计
2. 直接把这些数据补充进当前 workspace
3. 调用 ontology-registry-upsert
4. 告诉我目标实体是否已经可以用于后续异常分析
```

### Step B 的预期结果

运行日志里理想情况下应看到：

- `ontology-registry-upsert`

你要验证的点：

- assistant 明确说样例时序和告警已经写入
- workspace DB 仍然存在
- 没有意外生成新的 workspace

## 5. Step C：通过前端做异常诊断

如果 Step A 和 Step B 已经成功，这一步理论上可以不再上传文件。

可选上传：

- `maintenance_sop_excerpt.md`

推荐 prompt：

```text
继续使用当前 workspace。
请分析 MachineA 在 2026-07-03 14:00:00 到 2026-07-03 16:59:59 这个窗口的 power_kw 为什么异常。

要求：
1. 先解析实体和指标，不要直接猜
2. 目标实体按 MachineA / line3 / 3号产线相关上下文去解析
3. 当前窗口作为异常窗口
4. 基线窗口使用 2026-07-02 14:00:00 到 2026-07-02 16:59:59
5. 需要实际调用 ontology-runtime-resolve、timeseries-query-sql、energy-anomaly-diagnose
6. 给我证据链，不要只给结论
```

### Step C 的预期结果

运行日志里理想情况下应看到：

- `ontology-runtime-resolve`
- `timeseries-query-sql`
- `energy-anomaly-diagnose`

预期回答特征：

- 目标实体被解析成 `machine::machinea`
- 当前窗口平均值明显高于基线窗口
- 结果里提到关联告警事件
- 严重程度通常应为 `medium` 或 `high`

按当前这套 fixture 数据，一个比较合理的回答应大致能识别出：

- 异常窗口内功率明显升高
- `ALM-OVERLOAD` 与异常窗口重叠
- 这不是单个随机点波动，而是有持续性异常信号

## 6. Step D：验证 workspace resume 是否真的生效

做完 Step C 之后，你可以刷新浏览器，或者重新开一轮对话，但**仍然使用同一个 workspace**。

推荐 prompt：

```text
继续当前 workspace，不要从头开始。
先告诉我你是否还能看到已经建立的本体运行态和之前的分析上下文。
如果可以，请列出你会复用哪些已有状态，再决定下一步。
```

### Step D 的预期结果

你要关注：

- assistant 明确说它正在复用已有 workspace 状态
- 除非前面步骤真的失败，否则它不应该再要求你重新上传 schema 和 tags
- 如果它决定去读 artifact，运行日志里可能会出现 runtime artifact 相关工具调用

## 7. 怎么判断这次前端调试是成功的

当前前端手测的成功标准主要是这几条：

1. 同一个 `Workspace` 在多轮对话之间保持一致
2. 运行日志里能看到真实 skill 执行，而不是只有纯文本规划
3. `ontology_runtime.db` 确实出现在对应 workspace 目录下
4. 后续轮次能复用已有 workspace 状态，而不是每次重新建模
5. 异常分析轮次走的是 `resolve -> query -> diagnose`，而不是纯靠模型脑补

## 8. 当前前端还不够友好的地方

如果你从人工调试角度看，现在前端还有几个明显短板：

1. 没有 `agent_id` 选择器。
   你无法从页面强制指定 `industrial_ontology_designer`。

2. 没有显式 `skill_ids` 控件。
   你只能通过 prompt 去暗示模型。

3. 没有 artifact 浏览器。
   你必须自己去本地看 `artifacts/workspaces/<workspace_id>/`。

4. 没有阶段快捷入口。
   如果页面上有这种按钮会好很多：
   - 导入本体
   - 导入样例时序
   - 诊断异常

如果你的目标是稳定做人工调试，这 4 个前端增强项最值得优先补。

## 9. 目前这套数据已经够测什么

当前这套 fixture 已经足够验证下面这个前端 demo：

- 单台设备
- 单个功率测点
- 一个基线窗口
- 一个异常窗口
- 一个关联告警

这已经够覆盖：

- 本体持久化
- runtime resolve
- 时序查询
- 简单异常诊断

## 10. 目前这套数据还不够测什么

如果你希望前端 demo 更接近真实工业诊断流程，那当前数据仍然偏薄。

目前最高优先级缺失的数据有：

1. `line3_production_output.csv`
   用来支持 `specific_energy_kwh_per_t`，而不是只看 `power_kw`

2. `line3_work_orders.csv`
   用来把异常窗口和产品、工单、执行变更关联起来

3. `line3_meter_energy.csv`
   用来验证累计能耗，而不是只看瞬时功率

4. `line3_machine_status.csv`
   用来区分启动、空转、过载、控制不稳等状态

5. `line3_shift_calendar.csv`
   用来做班次对比

6. `line3_peer_machine_power.csv`
   用来做同线 peer 对比

## 11. 下一步最值得补哪些数据

如果你的下一目标是：

`为什么某个时间段的耗能异常`

那建议按这个顺序补数据：

1. `line3_production_output.csv`
2. `line3_meter_energy.csv`
3. `line3_work_orders.csv`
4. `line3_machine_status.csv`

这样前端测试就能从：

- 单点设备功率异常演示

往前走到：

- 产线级单吨能耗异常诊断，并且有更强的业务上下文支撑

## 12. 如果前端结果看起来不对，怎么快速排查

可以按下面的方式快速判断：

1. 如果没有任何 skill 被调用：
   说明 prompt 太泛了，要显式点名 harness 或 executable skill

2. 如果它每一轮都在重建：
   说明你的 workspace 变了，或者前一轮其实根本没有成功持久化

3. 如果它说无法诊断：
   大概率是 Step B 失败了，时序或告警数据没有真正写进 workspace DB

4. 如果它直接给结论但没有证据：
   重新发一轮，并明确要求它走 `resolve -> query -> diagnose`

5. 如果它要求重新上传之前已经上传过的文件：
   说明它没有正确复用 workspace 状态，或者前面的导入步骤其实失败了
