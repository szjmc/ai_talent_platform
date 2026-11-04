"""
模块：app/adapters/nl2sql/vanna_adapter.py
职责：Vanna.ai 适配（自然语言 -> SQL -> 数据 -> 图表配置）。

标签：[Vanna组] [P0]  依赖：SQLValidator + PgSQL 只读连接（ask -> SQL -> 只读执行 -> 图表）

============================ 实现思路与代码要点 ============================
流程：
    用户问题
      -> Vanna generate_sql(question, context=表结构/历史问答)
      -> sql_validator.validate_select_sql（必须）
      -> 只读连接执行
      -> 结果 -> 图表配置（Vanna get_plotly_figure 或自研 chart 推断）
      -> LLM 生成数据解读（insight）
实现要点：
- 训练数据（DDL、示例 SQL、业务术语）在 scripts/train_vanna.py 中导入；
- 分析类大查询应走 PgSQL 分析库（settings 增加 PGSQL_* 配置），不要打 MySQL 主库；
- 图表配置统一输出为 {type, x_field, y_field, title, series}，前端用 ECharts 渲染；
- 数据权限：生成 SQL 后追加角色数据范围约束，杜绝越权查询。
待实现清单：
    - [ ] VannaAdapter.ask(question, user_scope) -> NL2SQLResponse
    - [ ] 训练数据导入脚本
    - [ ] 图表类型推断（时间->折线、枚举->柱状、占比->饼图）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
