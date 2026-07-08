---
name: pg-risk-dataset-sync
description: 将本地 Excel 或 CSV 形式的企业涉法涉诉案件结构化数据幂等同步到 PostgreSQL，支持自动建表、按文件哈希跳过重复导入、按行指纹去重。适用于报告前先把司法案件、行政处罚等结构化数据补齐到持久库中的场景。
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
          description: 数据目录。未传时优先读取环境变量 PG_RISK_DATA_DIR，否则默认读取 skill 包内的 assets/input。
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
