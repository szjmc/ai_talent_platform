"""
模块：app/repositories/file_repository.py
职责：文件数据访问层。

标签：[业务组] [P1]（文件元数据）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 文件本体在磁盘/对象存储，表里只有元数据；
- file_hash 用于秒传与去重，重复上传返回已有记录；
- 孤儿文件清理由定时任务调用，不要放在请求链路里。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_by_hash(db, file_hash)                     秒传
    - [ ] list_by_biz(db, biz_type, biz_id)              业务关联文件
    - [ ] incr_download(db, file_id)                     下载计数 +1
    - [ ] list_orphans(db, before)                       孤儿文件（无业务关联 + 超期）
    - [ ] sum_size_by_biz(db, biz_type)                  存储占用统计

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
# from app.models.file import FileRecord

# TODO(负责人)：按上述方法清单实现。


class FileRepository(BaseRepository):
    """文件仓库（骨架）。"""

    # 注意：未指定 model 前调用任何方法都会抛 NotImplementedError
    # model = <Model>
    filterable_fields: set[str] = set()
    searchable_fields: set[str] = set()
    sortable_fields: set[str] = {"id", "created_at", "updated_at"}
    default_order = "-id"
