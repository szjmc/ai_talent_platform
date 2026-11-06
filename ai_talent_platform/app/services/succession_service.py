"""
模块：app/services/succession_service.py
职责：关键岗位继任方案 · 服务编排（取数 -> 定现任 -> 调规则引擎）。

标签：[Agent组] [P1]  依赖：AnalyticsRepository + succession_rules（规则引擎）

============================ 实现思路与代码要点 ============================
Service 的定位：把「取数 + 规则引擎」组织成一次完整业务，不写业务公式本身。

数据流：
    1) 从 PgSQL 分析库读人才宽表（匹配分/潜力/年限）
    2) 从测评历史算每个人的「测评达标率」（优秀+良好占比）
    3) 定「现任」：匹配分最高且年限≥7 的人（数据库暂无「在任岗位」标记，规则兜底）
    4) 其余人作为候选，喂给 succession_rules.build_succession_report 算准备度/风险/动作

约束：
- 只读分析库，不写 MySQL 主库，不经过 LLM（纯规则，可解释）；
- 权限范围只由后端从登录用户重算，不接受请求体传入数据范围；
- 缺数据不编造：现任/候选不足时明确报错，不硬凑。
=========================================================================
"""
from __future__ import annotations

from app.adapters.nl2sql.sql_validator import checked_scope
from app.ai.context import context_scope
from app.core.exceptions import BusinessException
from app.infrastructure.database.analytics_query import readonly_analytics_session
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.succession_rules import build_succession_report

# 「现任」识别阈值：匹配分达到该值且工作年限达到该值，才认定为关键岗位现任
_INCUMBENT_MIN_MATCH = 90.0
_INCUMBENT_MIN_YEARS = 7.0


class SuccessionService:
    """继任方案服务：取数 + 定现任 + 调规则引擎，纯规则不调 LLM。"""

    def __init__(self, db=None) -> None:
        # 保留公共 Service 构造约定；取数走分析库只读会话，不使用此 MySQL 会话。
        self.db = db

    async def generate(self, post_name: str, user) -> dict:
        """生成指定关键岗位的继任方案。

        post_name：关键岗位名（如「研发负责人」），仅用于展示，不参与取数过滤。
        user：认证用户，权限范围由后端重算。
        返回：继任报告 dict（可直接转 JSON 给前端）。
        """
        roles = self._roles(user)
        # 权限范围只由后端从登录用户计算，不接受请求体传入。
        with context_scope(user, roles=roles) as context:
            scope = checked_scope({
                "user_id": context.user_id,
                "roles": context.roles,
                "organization_id": context.organization_id,
            })
            # 继任方案涉及跨部门横向比较，仅允许全量范围（与现有分析口径一致）。
            if scope.get("data_scope") != "all":
                raise BusinessException("继任方案需全量数据权限，当前账号权限不足")

            async with readonly_analytics_session() as db:
                repository = AnalyticsRepository(db)
                wide = await repository.talent_wide_list()
                pass_rate = await repository.assessment_pass_rate()

        # 组装候选数据（宽表 + 测评达标率），再定现任
        candidates = []
        for row in wide:
            talent_id = row["talent_id"]
            candidates.append({
                "talent_id": talent_id,
                "name": row["name"],
                "match_score": float(row["match_avg_score"] or 0),
                "potential": row["potential_level"] or "中",
                "years": float(row["work_years"] or 0),
                "eval_rate": pass_rate.get(talent_id, 0.0),
            })

        incumbent = self._pick_incumbent(candidates)
        # 候选 = 除现任外的所有人
        rest = [c for c in candidates if c["talent_id"] != incumbent["talent_id"]]
        if not rest:
            raise BusinessException("数据不足：仅有现任，无候选可评估继任梯队")

        report = build_succession_report(post_name, incumbent, rest)
        return report

    @staticmethod
    def _pick_incumbent(candidates: list[dict]) -> dict:
        """定「现任」：匹配分最高且年限≥阈值的核心人才。

        数据库暂无「在任岗位/是否核心岗位」标记，用规则兜底：
        优先取「匹配分 ≥ 90 且 年限 ≥ 7」中匹配分最高者；无满足者则取全员匹配分最高者。
        """
        core = [c for c in candidates
                if c["match_score"] >= _INCUMBENT_MIN_MATCH and c["years"] >= _INCUMBENT_MIN_YEARS]
        pool = core or candidates
        # 匹配分最高；同分时年限更长者优先，保证确定性
        incumbent = max(pool, key=lambda c: (c["match_score"], c["years"]))
        return incumbent

    @staticmethod
    def _roles(user) -> list[str]:
        """取角色码；超级管理员统一规范化为 super_admin。"""
        roles = list(user.role_codes)
        return ["super_admin"] if user.is_superuser else roles
