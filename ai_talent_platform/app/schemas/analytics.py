"""
模块：app/schemas/analytics.py
职责：数据分析 / NL2SQL 相关请求 / 响应结构。

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. DashboardOverview     total_talents、active_positions、match_rate、training_completion、
                         assessment_avg_score、monthly_trend: list[{month, value}]
2. NL2SQLRequest         question、conversation_id(可空)、max_rows
3. NL2SQLResponse        question、sql(Vanna 生成，需脱敏展示)、
                         columns: list[str]、rows: list[list]、chart: dict(类型/配置)、
                         insight: str(AI 解读)、elapsed_ms
4. ChartConfig           type(bar/line/pie/radar/heatmap)、x_field、y_field、title
5. ReportRequest         report_type(monthly/quarterly/yearly/custom)、date_range、format(pdf/docx)
实现要点：
- SQL 必须经 adapters/nl2sql/sql_validator.py 校验（只允许 SELECT、禁止系统表、强制 limit）；
- 数据权限：生成 SQL 后追加角色数据范围条件（行级权限）；
- 大查询走 PgSQL（分析库），不要打到 MySQL 主库。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
