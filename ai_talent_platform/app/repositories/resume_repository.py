"""
模块：app/repositories/resume_repository.py
职责：简历数据访问层。

标签：[RAG组] [P0]（get_by_hash / list_by_talent / update_parse_result）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- parsed_data 为 JSON 列，结构与 ResumeParseResult.parsed 一致；
- 解析是异步流程：上传时 status=uploaded，工作流开始时=parsing，结束=success/failed；
- 同一 file_hash 重复上传直接复用已有记录（秒传）。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_by_hash(db, file_hash)                     秒传 / 查重
    - [ ] list_by_talent(db, talent_id)                  某人才的全部简历
    - [ ] update_parse_result(db, resume_id, parsed, accuracy)  写回解析结果
    - [ ] update_status(db, resume_id, status, error)    更新解析状态机
    - [ ] count_by_status(db)                            各状态数量（监控用）

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
=========================================================================
"""

from __future__ import annotations

from app.models.base import Base
from app.repositories.base import BaseRepository

# 模型实现后取消注释：
# from app.models.resume import Resume

# TODO(负责人)：按上述方法清单实现。


class ResumeRepository(BaseRepository):
    """简历仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
