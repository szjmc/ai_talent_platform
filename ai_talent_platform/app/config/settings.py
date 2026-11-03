"""
模块：app/config/settings.py
职责：全局配置中心（唯一配置来源）。

实现思路：
1. 使用 pydantic-settings 的 BaseSettings，自动从环境变量 / .env 文件加载配置；
2. 按「应用 -> 数据库 -> 缓存 -> 向量库 -> JWT/安全 -> 文件存储 -> AI」分组定义字段，
   每个字段给出安全默认值，保证没有 .env 也能本地启动（默认 SQLite，便于零依赖联调）；
3. 通过 @property 暴露派生配置（同步 DSN、跨域来源列表等），业务代码不要自己拼接字符串；
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
    CORS_ORIGINS: str = "*"

    # ------------------------------------------------------------------ 数据库
    # 开发默认使用 SQLite（aiosqlite），生产改为 MySQL：
    # mysql+aiomysql://user:password@127.0.0.1:3306/ai_talent_platform?charset=utf8mb4
    DB_DRIVER: Literal["mysql", "sqlite"] = "sqlite"
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "root"
    DB_NAME: str = "ai_talent_platform"
    DB_CHARSET: str = "utf8mb4"
    # SQLite 文件位置（DB_DRIVER=sqlite 时生效）
    SQLITE_PATH: str = os.path.join(BASE_DIR, "data", "app.db")

    DB_POOL_SIZE: int = 10               # 连接池常驻连接数
    DB_MAX_OVERFLOW: int = 20            # 峰值可额外申请的连接数
    DB_POOL_RECYCLE: int = 3600          # 连接回收时间（秒），避免 MySQL 8h 断连
    DB_ECHO: bool = False                # 是否打印 SQL（调试用）

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
    UPLOAD_DIR: str = os.path.join(BASE_DIR, "data", "uploads")
    MAX_UPLOAD_SIZE_MB: int = 20
    ALLOWED_UPLOAD_EXTENSIONS: str = ".pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg"

    # ------------------------------------------------------------------ 日志
    LOG_DIR: str = os.path.join(BASE_DIR, "logs")
    LOG_LEVEL: str = "INFO"
    LOG_ROTATION_MB: int = 20
    LOG_BACKUP_COUNT: int = 10

    # ------------------------------------------------------------------ 分页
    DEFAULT_PAGE: int = 1
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 200

    # ------------------------------------------------------------------ AI 能力层占位配置
    LLM_PROVIDER: Literal["qwen", "openai", "ollama"] = "qwen"
    LLM_MODEL: str = "qwen-plus"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2

    DIFY_BASE_URL: str = "http://127.0.0.1/v1"
    DIFY_API_KEY: str = ""

    # ------------------------------------------------------------------ 校验器
    @field_validator("API_PREFIX")
    @classmethod
    def _normalize_prefix(cls, v: str) -> str:
        """保证前缀以 / 开头、不以 / 结尾，避免路由拼接出现 //。"""
        v = "/" + v.strip("/")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------ 派生配置
    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """异步驱动 DSN（业务运行时使用）。"""
        if self.DB_DRIVER == "sqlite":
            os.makedirs(os.path.dirname(self.SQLITE_PATH) or ".", exist_ok=True)
            return f"sqlite+aiosqlite:///{self.SQLITE_PATH}"
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        """同步驱动 DSN（Alembic 迁移、初始化脚本等场景可选使用）。"""
        if self.DB_DRIVER == "sqlite":
            return f"sqlite:///{self.SQLITE_PATH}"
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的跨域配置解析为列表。"""
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_upload_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.ALLOWED_UPLOAD_EXTENSIONS.split(",") if e.strip()}

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"

    def ensure_dirs(self) -> None:
        """创建运行期需要的目录（日志、上传、SQLite 所在目录）。"""
        for path in (self.LOG_DIR, self.UPLOAD_DIR, os.path.dirname(self.SQLITE_PATH)):
            if path:
                os.makedirs(path, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """配置单例，便于测试时 `get_settings.cache_clear()` 后重新加载。"""
    return Settings()


settings: Settings = get_settings()
