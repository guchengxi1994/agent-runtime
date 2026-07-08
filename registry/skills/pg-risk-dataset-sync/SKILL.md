---
name: pg-risk-dataset-sync
description: "将本地 Excel 或 CSV 形式的企业涉法涉诉案件结构化数据幂等同步到 PostgreSQL，支持自动建表、按文件哈希跳过重复导入、按行指纹去重。通常直接使用 skill 自带的 assets/input 默认数据集，无需额外提供路径；仅在用户明确指定其他数据目录，或默认目录与环境变量目录都没有可读文件时才需要 data_dir。适用于报告前先把司法案件、行政处罚等结构化数据补齐到持久库中的场景。"
metadata:
  owner: runtime
  category: report-analysis
  stage: ingest
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        data_dir:
          type: string
          description: 可选的数据目录。通常不要因为缺少这个参数而追问用户；未传时先读环境变量 PG_RISK_DATA_DIR，若未设置或目录下没有可读文件，则自动回退到 skill 包内的 assets/input。只有用户明确要求切换数据集，或默认目录与环境目录都没有可读文件时，才需要传这个值。
        source_files:
          type: array
          description: 可选，显式指定要同步的文件路径或文件名。
          items:
            type: string
        table_name:
          type: string
          description: 目标事实表名。
          default: enterprise_risk_events
        import_log_table:
          type: string
          description: 导入日志表名。
          default: enterprise_risk_dataset_import_log
        dry_run:
          type: boolean
          description: 为 true 时只扫描和统计，不写入数据库。
          default: false
        force_rescan:
          type: boolean
          description: 为 true 时忽略同文件哈希跳过逻辑，重新执行导入并依赖行指纹去重。
          default: false
      additionalProperties: false
    execution_policy:
      timeout_ms: 180000
      idle_timeout_ms: 30000
      packages:
        - openpyxl==3.1.5
        - xlrd==2.0.1
        - psycopg[binary]==3.2.9
    required_secrets: {}
---

# PostgreSQL 涉法涉诉案件数据同步

把这个 executable skill 视为报告数据层的初始化步骤，而不是一次性导数脚本。

环境变量示例见同目录 `.env.example`。

## 默认调用规则

- `data_dir` 是可选参数，不是默认必填参数。
- 如果用户只是要求“导入当前案件 Excel 数据”“把这批案件数据同步到 PostgreSQL”“先把默认案件库灌进去”，直接调用这个 skill，参数可以为空。
- 不要仅因为用户没有提供 `data_dir` 就调用 `request_user_input`。
- 调用顺序应理解为：
  1. 优先使用显式传入的 `data_dir`
  2. 否则使用环境变量 `PG_RISK_DATA_DIR`
  3. 若环境变量未设置，或设置后目录内没有可读的 `.xlsx/.xls/.csv` 文件，则回退到 skill 包内的 `assets/input/`
- 只有在以下情况才需要追问路径：
  1. 用户明确说要导入另一批目录下的数据
  2. 默认目录和环境变量目录都没有可读文件
  3. 用户明确要求只导入某几个外部文件，而当前 `source_files` 无法解析

## 直接调用示例

- 如用户说“先把当前案件数据导入 PG”，直接调用 `pg-risk-dataset-sync`，可以不传任何参数。
- 如用户说“把 skill 自带的案件样例同步到 PG，后面我要出报告”，直接调用 `pg-risk-dataset-sync`，可以不传 `data_dir`。
- 如用户说“导入 D:\\datasets\\risk_cases 下面的新数据”，再传 `data_dir`。

适用方式：

- 报告依赖本地结构化 Excel 数据，但 PG 里还没有事实表时，先调用它。
- PG 已经有数据时，也可以继续调用；它会按文件哈希和行指纹做幂等跳过。
- 不要用它去删库重建。这个 skill 的目标是“补齐并保持数据可复用”。

当前默认约定：

- 默认数据集跟随 skill 一起打包，位于 `assets/input/`。
- 如果需要覆盖默认数据集，再通过 `data_dir` 或环境变量 `PG_RISK_DATA_DIR` 指向别的目录。
- 当前结构化数据会落到 `enterprise_risk_events`。
- 当前事实表优先覆盖：司法案件、行政处罚。

如果后续新增 12345、综治中心或其他来源，优先扩展这个 skill 的列映射和 `event_source`，而不是再写一套平行导入逻辑。
