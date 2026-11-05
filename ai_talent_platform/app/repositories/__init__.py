"""
模块：app/repositories/__init__.py
职责：数据访问层出口。

标签：[架构组] [P0]（数据访问层出口）

通用基类：BaseRepository（app/repositories/base.py）
    - 子类只需声明 model + 三个白名单字段，即可获得：
      get_by_id / get_by_id_or_404 / get_one / list / paginate / count /
      exists / create / create_many / update / bulk_update / delete / soft_delete

已实现：
    UserRepository          用户与角色
    OrganizationRepository  组织架构

待实现（每个文件开头已写明方法清单）：
    position / talent / resume / assessment / training / matching /
    knowledge / conversation / file / audit
"""

from app.repositories.base import BaseRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository

__all__ = ["BaseRepository", "UserRepository", "OrganizationRepository"]
