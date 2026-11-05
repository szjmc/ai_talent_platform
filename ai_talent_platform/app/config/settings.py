"""
模块：app/config/settings.py
职责：全局配置中心（唯一配置来源）。

标签：[架构组] [P0]（全局配置唯一来源，被所有模块依赖）

数据库方案（按需求文档「四库协同」，不使用 SQLite）：
    MySQL     业务主数据库：用户、组织、岗位、人才、测评等关系型主数据
    PgSQL     分析/时序数据库：海量履历、测评历史、行为时序、分析宽表（Vanna NL2SQL 查询目标）
    Milvus    向量数据库：人才/岗位/知识库向量（见 MILVUS_*）
    Redis     缓存/会话/限流（见 REDIS_*）

实现思路：
1. 使用 pydantic-settings 的 BaseSettings，自动从环境变量 / .env 文件加载配置；
2. 按「应用 → MySQL → PgSQL → Redis → Milvus → JWT/安全 → 文件 → 日志 → 分页 → AI」分组，
   每项给出可用的默认值（本地 MySQL/PgSQL 默认端口 + 常见账号），敏感值一律走环境变量；
3. 通过 @property / @computed_field 暴露派生 DSN（异步 / 同步两种驱动），业务代码不要自己拼字符串；
4. 全局单例 `settings = Settings()`，任何模块统一 `from app.config.settings import settings`。

约定：业务代码中禁止硬编码数据库地址、密钥、模型名称，一律从本文件读取。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：.../ai_talent_platform（本文件位于 ai_talent_platform/app/config/）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Settings(BaseSettings):
    """全局配置对象（环境变量优先，其次 .env，最后默认值）。"""

    model_config = SettingsConfigDict(
        env_file=(os.path.join(BASE_DIR, ".env"), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ 应用
    APP_NAME: str = "AI 数字化人才平台"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"          # 全平台统一的接口前缀
    DOCS_URL: str = "/docs"              # Swagger UI
    REDOC_URL: str = "/redoc"
    OPENAPI_URL: str = "/openapi.json"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 允许跨域的前端来源，逗号分隔；"*" 表示放开（仅调试用）
    # 注意：通配符模式下自动关闭 allow_credentials（浏览器会拒绝 *+credentials 组合，
    # 且平台使用 Bearer Token 并不依赖 Cookie，见 core/middleware.py）
    CORS_ORIGINS: str = "*"

    # 启动时是否允许 create_all 自动建表：仅 dev/test 生效，
    # prod 环境恒为 False（表结构变更一律走 Alembic，见 db_auto_create_tables）
    DB_AUTO_CREATE_TABLES: bool = True

    # ------------------------------------------------------------------ 限流
    RATE_LIMIT_ENABLED: bool = True        # 关闭后完全跳过限流中间件逻辑
    RATE_LIMIT_LIMIT: int = 300            # 窗口内允许的最大请求数（按客户端 IP）
    RATE_LIMIT_WINDOW_SECONDS: int = 60    # 统计窗口（秒）；Redis 启用时为多实例共享固定窗口

    # ================================================================== MySQL（业务主库）
    # 驱动固定为 mysql+aiomysql（异步）。本地默认：127.0.0.1:3306 / root。
    DB_DRIVER: Literal["mysql"] = "mysql"
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "root"
    DB_NAME: str = "ai_talent_platform"
    DB_CHARSET: str = "utf8mb4"

    DB_POOL_SIZE: int = 10               # 连接池常驻连接数
    DB_MAX_OVERFLOW: int = 20            # 峰值可额外申请的连接数
    DB_POOL_RECYCLE: int = 3600          # 连接回收时间（秒），避免 MySQL 8h 断连
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False                # 是否打印 SQL（调试用）

    # ================================================================== PgSQL（分析 / 时序库）
    # 用途：海量人才履历、测评历史、培训轨迹、行为时序、分析宽表；Vanna NL2SQL 的数据源。
    # 关闭（PGSQL_ENABLED=False）时，分析类功能自动降级为「仅统计 MySQL 数据」。
    PGSQL_ENABLED: bool = True
    PGSQL_HOST: str = "127.0.0.1"
    PGSQL_PORT: int = 5432
    PGSQL_USER: str = "postgres"
    PGSQL_PASSWORD: str = "postgres"
    PGSQL_DB: str = "ai_talent_analytics"
    PGSQL_SCHEMA: str = "public"
    PGSQL_POOL_SIZE: int = 5
    PGSQL_MAX_OVERFLOW: int = 10
    PGSQL_POOL_RECYCLE: int = 1800
    PGSQL_ECHO: bool = False

    # ---------------------------------------------------- 分析库同步（MySQL -> PgSQL）
    # 进程内调度（app/infrastructure/scheduler.py）：应用启动后按点执行增量同步。
    # 多 worker / 多实例靠 Redis 分布式锁互斥；Redis 未启用时退化为进程内锁（单实例语义）。
    ANALYTICS_SYNC_ENABLED: bool = False     # 默认关闭，避免开发环境反复跑全量
    ANALYTICS_SYNC_CRON_HOUR: int = 2        # 每日执行时点（0-23）
    ANALYTICS_SYNC_CRON_MINUTE: int = 0      # 每日执行分钟（0-59）
    ANALYTICS_SYNC_LOCK_TTL: int = 1800      # 同步锁 TTL（秒），超时自动释放防止死锁
    ANALYTICS_SYNC_RETRY: int = 3            # 失败重试次数（指数退避）

    # ------------------------------------------------------------------ Redis
    REDIS_ENABLED: bool = False          # 未部署 Redis 时自动降级为进程内缓存
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    REDIS_DEFAULT_TTL: int = 300

    # ------------------------------------------------------------------ Milvus
    MILVUS_ENABLED: bool = False
    MILVUS_URI: str = "http://127.0.0.1:19530"
    MILVUS_TOKEN: str = ""

    # ------------------------------------------------------------------ JWT / 安全
    JWT_SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440        # 访问令牌有效期（默认 1 天）
    JWT_REFRESH_EXPIRE_MINUTES: int = 10080  # 刷新令牌有效期（7 天）
    BCRYPT_ROUNDS: int = 12               # 密码哈希强度

    # 登录失败锁定策略
    LOGIN_MAX_FAIL_TIMES: int = 5
    LOGIN_LOCK_MINUTES: int = 15

    # ------------------------------------------------------------------ 文件存储
    # 存储后端：local（本地磁盘）/ minio（自建 MinIO）/ oss（阿里云 OSS）/ s3（通用 S3 兼容）
    # 业务代码统一通过 get_storage() 取用，切换后端只改本配置，不改调用方。
    STORAGE_BACKEND: Literal["local", "minio", "oss", "s3"] = "local"
    STORAGE_PUBLIC_URL: str = ""          # 对外访问基址（CDN / 反向代理），空则按各后端规则生成
    STORAGE_PRESIGN_EXPIRE_SECONDS: int = 3600
    STORAGE_TIMEOUT: int = 30             # 对象存储 HTTP 超时（秒）
    UPLOAD_DIR: str = os.path.join(BASE_DIR, "data", "uploads")
    MAX_UPLOAD_SIZE_MB: int = 20
    ALLOWED_UPLOAD_EXTENSIONS: str = ".pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg"

    # MinIO（S3 协议，默认 path-style 访问）
    MINIO_ENDPOINT: str = "127.0.0.1:9000"
    MINIO_SECURE: bool = False
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ai-talent"
    MINIO_REGION: str = "us-east-1"
    MINIO_PUBLIC_URL: str = ""            # 例如 https://files.example.com（公共读 / CDN）

    # 阿里云 OSS（Signature V1）
    OSS_ENDPOINT: str = "oss-cn-hangzhou.aliyuncs.com"
    OSS_SECURE: bool = True
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_BUCKET: str = ""
    OSS_PUBLIC_URL: str = ""

    # 通用 S3 兼容存储（AWS S3 / 其他兼容网关；MinIO 建议直接用 minio 后端）
    S3_ENDPOINT: str = ""
    S3_SECURE: bool = True
    S3_PATH_STYLE: bool = False
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    S3_PUBLIC_URL: str = ""

    # ------------------------------------------------------------------ 日志
    LOG_DIR: str = os.path.join(BASE_DIR, "logs")
    LOG_LEVEL: str = "INFO"
    LOG_ROTATION_MB: int = 20
    LOG_BACKUP_COUNT: int = 10

    # ------------------------------------------------------------------ 分页
    DEFAULT_PAGE: int = 1
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 200

    # ------------------------------------------------------------------ AI 能力层
    # LLM_PROVIDER 不写死枚举：内置 qwen / openai / deepseek / ollama，
    # 也可通过 llm_registry 注册自定义供应商（名称即注册键）。
    LLM_PROVIDER: str = "qwen"
    LLM_MODEL: str = "qwen-plus"          # 默认模型（留空则取供应商默认模型）
    LLM_API_KEY: str = ""                 # 通用密钥（仅当前供应商生效，专属密钥优先）
    LLM_BASE_URL: str = ""                # 通用地址（仅当前供应商生效，专属地址优先）
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    # 按用途覆盖模型："chat=deepseek-chat,talent=deepseek-reasoner"（支持 JSON 对象）
    LLM_MODEL_MAP: str = ""
    # 追加/覆盖模型降级链："deepseek-reasoner:deepseek-chat"
    LLM_FALLBACK_MODELS: str = ""
    # 向量模型供应商标识与模型（默认与 LLM_PROVIDER 相同；供应商不提供向量能力时必须指定）
    LLM_EMBED_PROVIDER: str = ""
    LLM_EMBED_MODEL: str = ""

    # 各供应商专属配置（语义：专属配置 > LLM_API_KEY / LLM_BASE_URL / LLM_MODEL > 内置默认）
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = ""
    DASHSCOPE_MODEL: str = ""
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = ""
    DEEPSEEK_MODEL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    OLLAMA_BASE_URL: str = ""
    OLLAMA_MODEL: str = ""

    DIFY_BASE_URL: str = "http://127.0.0.1/v1"
    DIFY_API_KEY: str = ""
    # Dify 对话应用 ID（[Agent组]+[Dify组] 共享开关：留空 = 总路由不启用 Dify 分支）
    DIFY_APP_ID_CHAT: str = ""

    # -------------------------------------------------------- Agent 总路由与调度
    # [Agent组] 新增配置项（默认值即可离线运行；仅追加，不改已有项）
    AGENT_MAX_STEPS: int = 8           # 状态图单次运行最大步数（防死循环保护）
    AGENT_ROUTE_LLM: bool = False      # 总路由是否启用 LLM 意图分类（默认关闭=纯规则路由）
    CONVERSATION_WINDOW: int = 10      # 对话记忆窗口（条）

    # ------------------------------------------------------------------ 校验器
    @field_validator("API_PREFIX")
    @classmethod
    def _normalize_prefix(cls, v: str) -> str:
        """保证前缀以 / 开头、不以 / 结尾，避免路由拼接出现 //。"""
        return "/" + v.strip("/")

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------ MySQL DSN
    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """MySQL 异步驱动 DSN（业务运行时使用）。"""
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def MYSQL_SERVER_URL(self) -> str:
        """不指定 database 的服务器级 DSN（用于 CREATE DATABASE）。"""
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/?charset={self.DB_CHARSET}"
        )

    # ------------------------------------------------------------------ PgSQL DSN
    @property
    def PGSQL_URL(self) -> str:
        """PgSQL 异步驱动 DSN（分析库）。"""
        return (
            f"postgresql+asyncpg://{self.PGSQL_USER}:{self.PGSQL_PASSWORD}"
            f"@{self.PGSQL_HOST}:{self.PGSQL_PORT}/{self.PGSQL_DB}"
        )

    @property
    def PGSQL_SERVER_URL(self) -> str:
        """不指定 database 的服务器级 DSN（默认连 postgres 库，用于 CREATE DATABASE）。"""
        return (
            f"postgresql+asyncpg://{self.PGSQL_USER}:{self.PGSQL_PASSWORD}"
            f"@{self.PGSQL_HOST}:{self.PGSQL_PORT}/postgres"
        )

    # ------------------------------------------------------------------ 通用派生配置
    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的跨域配置解析为列表。"""
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """CORS 是否允许携带凭证（Cookie / 同源凭据）。

        浏览器规范禁止 `Access-Control-Allow-Origin: *` 与
        `Access-Control-Allow-Credentials: true` 组合，因此通配符模式必须关闭凭证；
        本平台鉴权走 Authorization: Bearer，不依赖 Cookie，关闭无影响。
        """
        return "*" not in self.cors_origin_list

    @property
    def db_auto_create_tables(self) -> bool:
        """启动时是否允许 create_all 自动建表。

        生产环境（APP_ENV=prod）恒为 False —— 表结构变更一律走 Alembic，
        同时避免多 worker 启动时并发 DDL 竞争；dev/test 由 DB_AUTO_CREATE_TABLES 控制。
        """
        if self.is_prod:
            return False
        return self.DB_AUTO_CREATE_TABLES

    @property
    def allowed_upload_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.ALLOWED_UPLOAD_EXTENSIONS.split(",") if e.strip()}

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"

    def database_summary(self) -> dict[str, str]:
        """数据库连接摘要（脱敏，供日志 / 自检接口展示）。"""
        return {
            "mysql": f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}",
            "pgsql": (
                f"{self.PGSQL_HOST}:{self.PGSQL_PORT}/{self.PGSQL_DB}"
                if self.PGSQL_ENABLED
                else "disabled"
            ),
            "redis": self.REDIS_URL if self.REDIS_ENABLED else "disabled",
            "milvus": self.MILVUS_URI if self.MILVUS_ENABLED else "disabled",
        }

    @property
    def storage_summary(self) -> dict[str, str]:
        """文件存储摘要（脱敏，不含密钥），供 /system/info 展示。"""
        backend = self.STORAGE_BACKEND
        if backend == "minio":
            target = f"{self.MINIO_ENDPOINT}/{self.MINIO_BUCKET}"
        elif backend == "oss":
            target = f"{self.OSS_ENDPOINT}/{self.OSS_BUCKET or '-'}"
        elif backend == "s3":
            target = f"{self.S3_ENDPOINT or '-'}/{self.S3_BUCKET or '-'}"
        else:
            target = self.UPLOAD_DIR
        return {"backend": backend, "target": target}

    def ensure_dirs(self) -> None:
        """创建运行期需要的目录（日志、上传）。"""
        for path in (self.LOG_DIR, self.UPLOAD_DIR):
            if path:
                os.makedirs(path, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """配置单例，便于测试时 `get_settings.cache_clear()` 后重新加载。"""
    return Settings()


settings: Settings = get_settings()
