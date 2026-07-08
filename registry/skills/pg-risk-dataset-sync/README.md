调用 `pg-risk-dataset-sync`，把当前所有案件 Excel 数据写入 PostgreSQL。只做数据导入，不生成报告。

默认调用约定：

- `data_dir` 可以为空。
- 如果未传 `data_dir`，skill 会优先读取环境变量 `PG_RISK_DATA_DIR`。
- 如果环境变量未设置，或该目录下没有可读的 `.xlsx/.xls/.csv` 文件，skill 会自动回退到当前 skill 包内的 `assets/input/`。
- 因此，当任务只是“导入当前 bundled 数据集”时，直接调用即可，不需要先追问路径。

示例：

- “把当前案件 Excel 数据导入 PG。”
- “先同步默认案件数据，后面再生成报告。”
