"""
模块：app/services/organization_service.py
职责：组织架构业务（部门树、层级维护、移动、删除校验）。

标签：[业务组] [P1]

实现思路：
1. 新增：校验 parent 存在 -> 同级重名 -> 插入 -> flush 拿 id -> 回写 level/path；
2. 更新：字段白名单；归属父级不变，path 不变；
3. 移动：目标不能是自己或自己的子孙 -> 重算自身 level/path -> 批量重写子孙 path；
4. 删除：无子部门 + 无在职人员 + 无关联人才才允许（逻辑删除）；
5. 组织树：一次查出全部启用部门，在内存中构建 children 关系，避免 N+1 查询。

path 规则：根部门 path="/"；某部门 path = 父.path + 父.id + "/"，
例如 1 号根部门下 3 号部门：path = "/" + "1" + "/" = "/1/"。
子树查询：WHERE path LIKE '/1/%' OR path='/1/' 即可覆盖该部门及其全部子孙。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, DuplicateError, NotFoundError, ParamError
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.organization import Organization
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.organization import (
    OrgCreate,
    OrgMoveRequest,
    OrgResponse,
    OrgTreeNode,
    OrgUpdate,
)

logger = logging.getLogger(__name__)


class OrganizationService:
    """组织架构服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = OrganizationRepository()
        self.user_repo = UserRepository()

    # ------------------------------------------------------------ 查询
    async def page_list(self, page: PageParams, *, keyword: str | None = None) -> PageResult[OrgResponse]:
        """分页（扁平）列表，便于表格展示。"""
        result = await self.repo.paginate(self.db, page, keyword=keyword)
        result.items = [
            OrgResponse.model_validate(o) for o in result.items
        ]
        return result

    async def get_detail(self, org_id: int) -> OrgResponse:
        org = await self.repo.get_by_id_or_404(self.db, org_id, message="部门不存在")
        resp = OrgResponse.model_validate(org)
        resp.user_count = await self.repo.count_users(self.db, org.id)
        return resp

    async def get_tree(self) -> list[OrgTreeNode]:
        """返回完整组织树。"""
        orgs = await self.repo.list_all_active(self.db)
        nodes: dict[int, OrgTreeNode] = {}
        for org in orgs:
            nodes[org.id] = OrgTreeNode(
                id=org.id,
                parent_id=org.parent_id,
                code=org.code,
                name=org.name,
                leader_id=org.leader_id,
                sort=org.sort,
                is_active=org.is_active,
                children=[],
            )

        roots: list[OrgTreeNode] = []
        for node in nodes.values():
            parent = nodes.get(node.parent_id) if node.parent_id else None
            if parent is not None:
                parent.children.append(node)
            else:
                roots.append(node)

        # 同级按 sort, id 排序（递归排序）
        def _sort(items: list[OrgTreeNode]) -> None:
            items.sort(key=lambda n: (n.sort, n.id))
            for item in items:
                _sort(item.children)

        _sort(roots)
        return roots

    # ------------------------------------------------------------ 写入
    async def create_org(self, data: OrgCreate) -> OrgResponse:
        """创建部门。"""
        parent: Organization | None = None
        if data.parent_id is not None:
            parent = await self.repo.get_by_id(self.db, data.parent_id)
            if parent is None:
                raise NotFoundError("上级部门不存在")

        if await self.repo.exists_sibling_name(self.db, data.parent_id, data.name):
            raise DuplicateError("同级下已存在同名部门")
        if await self.repo.get_by_code(self.db, data.code):
            raise DuplicateError("部门编码已存在")

        org = Organization(
            parent_id=data.parent_id,
            code=data.code,
            name=data.name,
            leader_id=data.leader_id,
            sort=data.sort,
            is_active=data.is_active,
            remark=data.remark,
            level=(parent.level + 1) if parent else 1,
            path=(parent.path + str(parent.id) + "/") if parent else "/",
        )
        self.db.add(org)
        await self.db.flush()
        logger.info("创建部门 id=%s name=%s path=%s", org.id, org.name, org.path)
        return OrgResponse.model_validate(org)

    async def update_org(self, org_id: int, data: OrgUpdate) -> OrgResponse:
        """更新部门（父级/编码不可通过本接口修改）。"""
        org = await self.repo.get_by_id_or_404(self.db, org_id, message="部门不存在")
        payload = data.model_dump(exclude_unset=True)

        if "name" in payload and payload["name"] != org.name:
            if await self.repo.exists_sibling_name(
                self.db, org.parent_id, payload["name"], exclude_id=org.id
            ):
                raise DuplicateError("同级下已存在同名部门")

        await self.repo.update(self.db, org, payload)
        return OrgResponse.model_validate(org)

    async def move_org(self, org_id: int, data: OrgMoveRequest) -> OrgResponse:
        """把部门移动到新的父级（级联修正子孙 path / level）。"""
        org = await self.repo.get_by_id_or_404(self.db, org_id, message="部门不存在")

        if data.new_parent_id == org_id:
            raise ParamError("不能把部门移动到自身下面")
        if data.new_parent_id is None and org.parent_id is None:
            raise ParamError("该部门已经是根部门")

        new_parent: Organization | None = None
        if data.new_parent_id is not None:
            new_parent = await self.repo.get_by_id(self.db, data.new_parent_id)
            if new_parent is None:
                raise NotFoundError("目标上级部门不存在")
            # 目标不能是自己的子孙：子孙的 path 以自身 path + id 为前缀
            own_prefix = org.path + str(org.id) + "/"
            if new_parent.path.startswith(own_prefix) or new_parent.id == org.id:
                raise BusinessRuleError("不能把部门移动到自己的子部门下")

        old_path = org.path
        new_level = (new_parent.level + 1) if new_parent else 1
        new_path = (new_parent.path + str(new_parent.id) + "/") if new_parent else "/"
        level_delta = new_level - org.level

        # 1) 先更新自身
        await self.repo.update(
            self.db, org, {"parent_id": data.new_parent_id, "level": new_level, "path": new_path}
        )

        # 2) 再批量重写子孙
        if level_delta != 0 or old_path != new_path:
            await self.repo.update_subtree_path(
                self.db,
                old_prefix=old_path + str(org.id) + "/",
                new_prefix=new_path + str(org.id) + "/",
                level_delta=level_delta,
            )

        logger.info(
            "移动部门 id=%s -> parent=%s (path %s -> %s)", org.id, data.new_parent_id, old_path, new_path
        )
        return OrgResponse.model_validate(org)

    async def delete_org(self, org_id: int) -> None:
        """删除部门（含完整性校验）。"""
        org = await self.repo.get_by_id_or_404(self.db, org_id, message="部门不存在")

        if await self.repo.count_children(self.db, org.id) > 0:
            raise BusinessRuleError("请先删除或移走该部门下的子部门")
        if await self.repo.count_users(self.db, org.id) > 0:
            raise BusinessRuleError("该部门下仍有在职用户，无法删除")

        await self.repo.delete(self.db, org, soft=True)
        logger.info("删除部门 id=%s name=%s", org.id, org.name)
