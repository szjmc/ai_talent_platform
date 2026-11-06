"""
模块：app/core/application.py
职责：FastAPI 应用工厂（把公共能力组装成一个可运行的应用）。

标签：[架构组] [P0]（应用工厂：中间件 / 异常 / 路由装配）

实现思路（组装顺序很重要）：
1. 创建 FastAPI 实例：标题、版本、描述、doc/openapi 地址、lifespan；
2. 配置 Swagger 的 Bearer 鉴权按钮（Authorize 后可直接调试需登录接口）；
3. 注册中间件（trace/日志/CORS/限流/安全头）；
4. 注册全局异常处理；
5. 注册总路由（app/routers/router.py 中的 api_router，统一前缀 /api/v1）；
6. 注册根路由与健康检查（/、/health、/health/ready）；
7. 统一给所有路由加 tags 排序、OpenAPI 元数据。

使用：
    main.py 中 `app = create_application()`；
    测试中可直接 `app = create_application()` 后用 TestClient 覆盖依赖。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.lifespan import lifespan
from app.core.middleware import register_middlewares
from app.core.response import success
from app.infrastructure.database.session import check_all_connections

logger = get_logger(__name__)

DESCRIPTION = """
### AI 数字化人才平台 · 后端 API

本项目采用「职责分层架构」：`routers → schemas → services → repositories → models`，
AI 能力独立在 `ai/`，第三方能力在 `adapters/`，底层资源在 `infrastructure/`。

**统一响应格式**

```json
{ "code": 200, "message": "success", "data": {}, "trace_id": "a1b2c3" }
```

**鉴权方式**：先在 `/api/v1/auth/login` 获取 `access_token`，
点击右上角 Authorize 填入后即可调试需要登录的接口。
"""

TAGS_METADATA = [
    {"name": "系统", "description": "健康检查、版本信息"},
    {"name": "认证", "description": "注册、登录、刷新令牌、当前用户"},
    {"name": "用户管理", "description": "用户 CRUD、状态管理、角色分配"},
    {"name": "组织架构", "description": "部门树、层级维护"},
    {"name": "岗位管理", "description": "岗位信息与能力要求"},
    {"name": "人才档案", "description": "人才档案、画像、语义检索"},
    {"name": "简历管理", "description": "简历上传、AI 解析"},
    {"name": "人才测评", "description": "测评任务、答题、结果分析"},
    {"name": "培训管理", "description": "培训计划、课程、学习记录"},
    {"name": "人岗匹配", "description": "向量匹配、匹配解释"},
    {"name": "知识库", "description": "知识文档与 RAG 检索"},
    {"name": "数据分析", "description": "统计指标、NL2SQL 问数"},
    {"name": "AI 智能体", "description": "Agent 对话与任务执行"},
    {"name": "文件管理", "description": "文件上传、下载、预览"},
]


def _custom_openapi(app: FastAPI) -> dict:
    """自定义 OpenAPI：加入 Bearer 鉴权、服务器地址、标签说明。"""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=DESCRIPTION,
        routes=app.routes,
        tags=TAGS_METADATA,
        servers=[{"url": "/", "description": "当前服务"}],
    )
    # 复用 FastAPI 由 HTTPBearer 依赖（core/dependencies.py）自动生成的「HTTPBearer」方案。
    # 不要再手动注入 BearerAuth，否则 Swagger 的 Authorize 弹窗会出现两个重复输入框。
    security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    if "HTTPBearer" in security_schemes:
        security_schemes["HTTPBearer"]["description"] = "登录接口返回的 access_token"
        security_schemes["HTTPBearer"].setdefault("bearerFormat", "JWT")
    # 全局默认使用 Bearer 鉴权（登录/注册/刷新用 openapi_extra={"security": []} 关闭）
    schema["security"] = [{"HTTPBearer": []}]
    app.openapi_schema = schema
    return schema


def create_application() -> FastAPI:
    """应用工厂。"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=DESCRIPTION,
        docs_url=settings.DOCS_URL,
        redoc_url=settings.REDOC_URL,
        openapi_url=settings.OPENAPI_URL,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        debug=settings.DEBUG,
        swagger_ui_parameters={
            "persistAuthorization": True,   # 刷新页面保留 Token
            "displayRequestDuration": True, # 展示接口耗时
            "docExpansion": "none",
            "filter": True,
        },
    )

    # 1) 公共能力
    register_middlewares(app)
    register_exception_handlers(app)

    # 2) 总路由（必须最后注册，确保中间件/异常处理对其生效）
    from app.routers.router import api_router

    app.include_router(api_router, prefix=settings.API_PREFIX)

    # 2.1) 候选人笔试内置考试页（不挂 /api/v1，供邮件链接直接打开）
    from app.routers.exam_router import exam_page_router

    app.include_router(exam_page_router)

    # 3) 系统级路由（不放在 /api/v1 下，方便运维探测）
    @app.get("/", tags=["系统"], summary="服务信息", include_in_schema=False)
    async def root():
        return success(
            data={
                "name": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "env": settings.APP_ENV,
                "docs": settings.DOCS_URL,
                "api_prefix": settings.API_PREFIX,
            },
            message="服务运行中",
        )

    @app.get("/health", tags=["系统"], summary="健康检查（存活）")
    async def health():
        """存活探针：只表示进程可用，不检查下游依赖。"""
        return success(
            data={
                "status": "up",
                "version": settings.APP_VERSION,
                "env": settings.APP_ENV,
                "databases": settings.database_summary(),
            },
            message="OK",
        )

    @app.get("/health/ready", tags=["系统"], summary="健康检查（就绪）")
    async def readiness():
        """就绪探针：检查 MySQL / PgSQL 等关键依赖。

        MySQL 是业务主库，不可用时返回 503；
        PgSQL 为分析库，不可用时状态为 degraded（业务接口仍可用，分析类功能降级）。
        """
        report = await check_all_connections()
        payload = {
            **report,
            "drivers": {
                "mysql": "mysql+aiomysql",
                "pgsql": "postgresql+asyncpg" if settings.PGSQL_ENABLED else "disabled",
            },
        }

        if report["status"] == "down":
            # MySQL 是业务主库：不可用时返回 503，并给出可读原因与修复建议
            return JSONResponse(
                status_code=503,
                content={
                    "code": 503,
                    "message": f"MySQL 不可用：{report['mysql']['message']}",
                    "data": payload,
                },
            )
        return success(
            data=payload,
            message="OK" if report["status"] == "up" else "MySQL 正常，PgSQL 不可用（分析功能降级）",
        )

    # 4) OpenAPI 定制
    app.openapi = lambda: _custom_openapi(app)  # type: ignore[method-assign]

    logger.info("FastAPI 应用创建完成：%s v%s", settings.APP_NAME, settings.APP_VERSION)
    return app
