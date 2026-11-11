"""
模块：app/config/settings.py
职责：全局配置中心（唯一配置来源）。

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

标签：[架构组] [P0]（全局配置唯一来源，被所有模块依赖）
"""

from __future__ import annotations

import os
import json  # noqa: F401 - 供 .env 中 JSON 形式的配置项直接粘贴（DIFY 校验器已移除，保留导入不影响运行）
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

    # 前端站点地址（用于邮件里的笔试入口链接指向 {FRONTEND_URL}/exam/{id}），如 https://www.example.com
    # 若前端已实现考试页则优先使用；留空则回退到 PUBLIC_BASE_URL（后端内置考试页）。
    FRONTEND_URL: str = ""

    # 后端对外的公网访问基址（候选人通过邮件链接打开内置考试页时的域名），如 https://api.example.com
    # 必填项：邮件里的笔试链接 = {PUBLIC_BASE_URL}/exam/{id}?token=...，
    # 若 FRONTEND_URL 与 PUBLIC_BASE_URL 均为空，则退回请求 Host（仅调试可用，生产环境点不开）。
    PUBLIC_BASE_URL: str = ""

    # ------------------------------------------------------------------ 邮件推送（测评组：笔试/面试邀约通知）
    # 个人邮箱 SMTP 方案（QQ/163/企业邮箱 + 授权码），无需企业认证。
    # EMAIL_ENABLED=false 或 SMTP 配置缺失时明确返回未发送，不模拟成功。
    EMAIL_ENABLED: bool = False
    SMTP_HOST: str = ""            # 如 smtp.qq.com / smtp.163.com
    SMTP_PORT: int = 465
    SMTP_USE_SSL: bool = True
    SMTP_TLS: bool = False         # 非 SSL 端口可启用 STARTTLS，与 SMTP_USE_SSL 互斥
    SMTP_USER: str = ""            # 登录账号（发件地址强制取此值，QQ/163 要求发件=登录）
    SMTP_PASSWORD: str = ""        # SMTP 授权码（非邮箱登录密码）
    SMTP_FROM: str = ""            # 历史兼容字段，实际发件地址统一取 SMTP_USER
    EMAIL_FROM_NAME: str = "AI人才测评中心"

    # ------------------------------------------------------------------ 面试记录表视觉模型（拍照上传 -> 结构化解析）
    LLM_VISION_MODEL: str = ""     # 多模态视觉模型名，如 qwen-vl-plus / Qwen/Qwen2.5-VL-72B-Instruct

    # 面试记录表本地哈希嵌入维度（独立于 RAG 组 EMBEDDING_DIM，固定 128）
    EMBED_DIM: int = 128

    # ================================================================== MySQL（业务主库）
    # 驱动固定为 mysql+aiomysql（异步）。本地默认：127.0.0.1:3306 / root。
    # DB_DRIVER=sqlite 时为「本地开发/演示回退」：业务库落到单文件 SQLite（aiosqlite），
    # 便于没有 MySQL 的环境（沙箱 / 快速体验）一键跑通全部页面；生产仍然只用 MySQL。
    DB_DRIVER: Literal["mysql", "sqlite"] = "mysql"
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "root"
    DB_NAME: str = "ai_talent_platform"
    DB_CHARSET: str = "utf8mb4"
    # SQLite 模式下的库文件位置（相对项目根目录；目录不存在会自动创建）
    DB_SQLITE_FILE: str = "data/talent_platform.db"

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
    # 缓存/会话/限流/NL2SQL查询缓存/分布式锁统一使用此配置。
    # 部署时只需修改 REDIS_URL（格式：redis://[:password@]host:port/db 或 rediss://... for SSL）。
    # 连接失败时会自动降级到 fakeredis（开发/单实例）/内存缓存，业务无感；启动日志会打印实际后端 kind=redis|fakeredis|memory。
    REDIS_ENABLED: bool = True                 # 设为 False 强制只用进程内内存缓存（不推荐生产）
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    REDIS_PASSWORD: str = ""                   # 如 URL 已带密码可留空；会合并到 URL 覆盖
    REDIS_DB: int = 0                          # 默认 DB，URL 中的 /db 优先级更高
    REDIS_DEFAULT_TTL: int = 300               # 默认缓存 TTL（秒）
    REDIS_KEY_PREFIX: str = "aitp:"            # 所有业务 key 统一前缀，避免与其他应用冲突
    REDIS_MAX_CONNECTIONS: int = 50            # 连接池上限
    REDIS_SOCKET_CONNECT_TIMEOUT: float = 3.0  # 连接超时（秒）；防止启动时卡住
    REDIS_SOCKET_TIMEOUT: float = 5.0          # 读写超时（秒）
    REDIS_SSL: bool = False                    # 云 Redis（如阿里云/腾讯云 TLS）设为 True

    # ------------------------------------------------------------------ Milvus
    MILVUS_ENABLED: bool = False
    MILVUS_URI: str = "http://127.0.0.1:19530"
    MILVUS_TOKEN: str = ""
    # 复用部署已有物理集合，业务请求不重建集合。

    # 3 个物理集合名称 + 分词器
    MILVUS_COLLECTION_TALENT: str = "talent_chunks"
    MILVUS_COLLECTION_KNOWLEDGE: str = "knowledge_chunks"
    MILVUS_COLLECTION_QUESTIONS: str = "question_bank_vectors"
    MILVUS_COLLECTION_ASSESSMENT: str = "assessment_chunks"
    MILVUS_ANALYZER_TYPE: str = "chinese"

    # ------------------------------------------------------------------ 硅基流动 Embedding
    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_BASE_URL: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-large-zh-v1.5"

    # ------------------------------------------------------------------ Webhook 回调（供 C 建立 BM25）
    C_WEBHOOK_BM25_URL: str = ""
    C_WEBHOOK_BM25_TOKEN: str = ""

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

    # 存储后端：local（本地磁盘）/ minio（自建 MinIO）/ oss（阿里云 OSS）/ s3（通用 S3 兼容）
    # 业务代码统一通过 get_storage() 取用，切换后端只改本配置，不改调用方。[架构组合并]
    STORAGE_BACKEND: Literal["local", "minio", "oss", "s3"] = "local"
    STORAGE_PUBLIC_URL: str = ""          # 对外访问基址（CDN / 反向代理），空则按各后端规则生成
    STORAGE_PRESIGN_EXPIRE_SECONDS: int = 3600
    STORAGE_TIMEOUT: int = 30             # 对象存储 HTTP 超时（秒）
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
    # 也可通过 llm_registry 注册自定义供应商（名称即注册键）。[架构组合并]
    LLM_PROVIDER: str = "qwen"
    LLM_MODEL: str = "qwen-plus"          # 默认模型（留空则取供应商默认模型）
    LLM_API_KEY: str = ""                 # 通用密钥（仅当前供应商生效，专属密钥优先）
    LLM_BASE_URL: str = ""                # 通用地址（仅当前供应商生效，专属地址优先）
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    # 按用途覆盖模型："chat=deepseek-chat,talent=deepseek-reasoner"（支持 JSON 对象）[架构组合并]
    LLM_MODEL_MAP: str = ""
    # 追加/覆盖模型降级链："deepseek-reasoner:deepseek-chat" [架构组合并]
    LLM_FALLBACK_MODELS: str = ""
    # 向量模型供应商标识与模型（默认与 LLM_PROVIDER 相同；供应商不提供向量能力时必须指定）
    # RAG 批量和维度校验配置；实际模型维度必须与远程服务一致。
    # 补偿表初始化后启用；关闭时维持原入库行为。
    RAG_DURABLE_INGEST_ENABLED: bool = False
    # 简历自动入库的周期性补扫：后台任务是进程内的，进程重启/任务丢失时靠它补齐。
    # 补扫基于数据状态判断（已成功/待复核的版本不会重复处理），可安全周期执行。
    RESUME_INGEST_SWEEP_ENABLED: bool = True
    # 未配置向量供应商（或无 Embedding 密钥）时，是否允许仍把版本发布为「仅词法检索可用」。
    # true：发布成功但跳过 Milvus 写入，doc.index_error 明确提示；配置密钥后重建索引即可补齐向量。
    # false：发布直接失败（严格模式，适合必须保证语义检索可用的生产库）。
    RAG_ALLOW_PUBLISH_WITHOUT_EMBEDDING: bool = True
    OCR_ENABLED: bool = True
    OCR_MIN_CONFIDENCE: float = Field(default=0.5, ge=0, le=1)
    OCR_MAX_PIXELS: int = Field(default=20000000, ge=1)
    OCR_PDF_SCALE: float = Field(default=2.0, ge=1, le=4)
    DOCUMENT_MAX_PAGES: int = Field(default=200, ge=1)
    RERANK_MODEL: str = ""
    RERANK_API_KEY: str = ""
    RERANK_BASE_URL: str = ""
    RERANK_TIMEOUT: int = Field(default=30, ge=1)
    CHUNK_STRATEGY: Literal["recursive", "semantic"] = "recursive"
    SEMANTIC_SPLIT_THRESHOLD: float = Field(default=0.65, ge=-1, le=1)
    CHUNK_SIZE: int = Field(default=500, ge=1)
    CHUNK_OVERLAP: int = Field(default=100, ge=0)
    EMBEDDING_DIM: int = Field(default=1024, ge=1)
    EMBEDDING_BATCH_SIZE: int = Field(default=32, ge=1, le=100)
    LLM_EMBED_PROVIDER: str = ""
    # 向量模型（维度必须与 Milvus collection 一致：bge-m3=1024 / text-embedding-v3=1024）
    LLM_EMBED_MODEL: str = ""
    # 向量维度（建集合与校验用；换模型时必须同步改）
    LLM_EMBED_DIM: int = 1024

    # 各供应商专属配置（语义：专属配置 > LLM_API_KEY / LLM_BASE_URL / LLM_MODEL > 内置默认）[架构组合并]
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

    # 说明：origin/lz 侧此处为 os.getenv 写法且注解为 dict[str, str]，
    #       但两套适配器（dify_adapter / workflow_adapter）均按 str 使用，
    #       None 默认值会让 .rstrip() 报错，故保留 str 语义与默认地址。
    DIFY_BASE_URL: str = "http://127.0.0.1/v1"
    # 保留字符串口径：本分支 dify_adapter / explanation_service 按 str 拼 Bearer 头；
    # lz 分支的工作流密钥改由 models/dify.py 的 wf_apikey 数据库字段提供，不读本配置，
    # 故此处维持 str，避免鉴权头被序列化成 dict 文本。
    DIFY_API_KEY: str = ""
    # 两条 Workflow 各用自己的应用 Key；空时兼容共用 Key。
    # [岗位匹配接入] JD解析与匹配解释分别配置Key，空值兼容原公共Key。
    DIFY_JD_API_KEY: str = ""
    DIFY_EXPLANATION_API_KEY: str = ""
    # Dify 对话应用 ID（[Agent组]+[Dify组] 共享开关：留空 = 总路由不启用 Dify 分支）
    DIFY_APP_ID_CHAT: str = ""
    # 测评工作流标识；留空时明确拒绝相应外部能力，不使用测试数据兜底。
    DIFY_WF_ASSIGN: str = ""
    DIFY_WF_SCORE: str = ""
    DIFY_WF_ANALYZE: str = ""
    DIFY_WF_REPORT: str = ""

    # ------------------------------------------------------------------ Vanna 最小问数链路 [架构组合并]
    NL2SQL_MAX_ROWS: int = Field(1000, ge=1, le=1000)
    NL2SQL_TIMEOUT_SECONDS: int = Field(60, ge=1, le=120)
    NL2SQL_ALLOWED_SCHEMA: str = "public"
    # 运行期只读凭据与建表使用的 PGSQL_USER/PASSWORD 分离；为空时明确不可用。
    NL2SQL_PGSQL_USER: str = "admin"
    NL2SQL_PGSQL_PASSWORD: str = "123456"
    VANNA_MEMORY_PATH: str = os.path.join(BASE_DIR, "data", "vanna", "chroma")

    # -------------------------------------------------------- Agent 总路由与调度
    # [Agent组] 新增配置项（默认值即可离线运行；仅追加，不改已有项）
    AGENT_MAX_STEPS: int = 8           # 状态图单次运行最大步数（防死循环保护）
    AGENT_ROUTE_LLM: bool = False      # 总路由是否启用 LLM 意图分类（默认关闭=纯规则路由）
    CONVERSATION_WINDOW: int = 10      # 对话记忆窗口（条）

       # -------------------------------------------------------- Agent 总路由与调度
    # [Agent组] 新增配置项（默认值即可离线运行；仅追加，不改已有项）
    AGENT_MAX_STEPS: int = 8           # 状态图单次运行最大步数（防死循环保护）
    AGENT_ROUTE_LLM: bool = False      # 总路由是否启用 LLM 意图分类（默认关闭=纯规则路由）
    CONVERSATION_WINDOW: int = 10      # 对话记忆窗口（条）

    # ------------------------------------------- 人才邀约 / 笔试 / 面试间
    # [Agent组] 邀约配置；SMTP 统一使用上方唯一配置块，避免端口/加密方式互相覆盖。
    INVITE_BASE_URL: str = ""                       # 候选人公开页前缀；生产必须显式配置
    INVITE_TOKEN_TTL_HOURS: int = 72                # 邀约链接有效期（小时）
    INVITE_EXAM_DURATION_MINUTES: int = 60          # 笔试默认时长（分钟）
    INVITE_INTERVIEW_GRACE_MINUTES: int = 15        # 面试可提前进场时间
    INVITE_INTERVIEW_END_GRACE_MINUTES: int = 180   # 面试可延后离场时间
    SMS_PROVIDER: str = ""                          # 留空表示未接入真实网关，禁止模拟成功
    SMS_ACCESS_KEY: str = ""
    SMS_ACCESS_SECRET: str = ""
    PROCTOR_YOLO_MODEL: str = ""                    # yolov8n.onnx 路径；空 = 自动探测 models/ 目录
    PROCTOR_INTERVAL_SECONDS: int = 5               # 前端监考帧上报间隔（秒）
    WEBRTC_ICE_SERVERS: str = '[{"urls":"stun:stun.l.google.com:19302"}]'

    # ------------------------------------------- 在线监考 · 行为与环境防作弊（[Agent组]）
    # 视觉检测只能看画面内的东西；切屏 / 远程控制 / 投屏 / 多显示器 / 虚拟摄像头
    # 这些「画面外」作弊必须由浏览器信号 + 服务端判定，配置项见 ai/proctoring/integrity.py。
    PROCTOR_ENABLED: bool = True                     # 行为监考总开关（关闭后不再要求上报与共享）
    PROCTOR_STRICT_MODE: bool = True                 # 严格模式：命中硬性风险（远程控制/投屏/代考）即阻断作答
    PROCTOR_REQUIRE_SCREEN_SHARE: bool = False        # 是否强制候选人共享整个屏幕（笔试作弊高发场景建议开启）
    PROCTOR_HEARTBEAT_SECONDS: int = 15              # 前端监考心跳间隔（秒）
    PROCTOR_HEARTBEAT_TIMEOUT_SECONDS: int = 60      # 心跳超过该时长未更新即判 heartbeat_lost
    PROCTOR_SNAPSHOT_INTERVAL_SECONDS: int = 20      # 屏幕/摄像头快照抽样间隔（秒，留给人工复核）
    PROCTOR_SNAPSHOT_MAX_PER_SESSION: int = 15       # 单个会话最多留存的可疑快照数（控存储成本）
    PROCTOR_RISK_BLOCK_SCORE: int = 85               # 风险分达到该值后禁止继续作答/作答无效
    PROCTOR_MAX_EVENTS_PER_REPORT: int = 30          # 单次上报允许的最大事件条数（防刷分）
    PROCTOR_ALLOW_MULTI_DISPLAY: bool = False        # 是否允许候选人使用多显示器（默认不允许）

    # ------------------------------------------- AI 数字人面试（[Agent组]）
    # 说明：面试链接复用 INVITE_BASE_URL / FRONTEND_URL / PUBLIC_BASE_URL 的优先级，
    #      未配置时退回请求 Host（仅调试可用）。
    AI_INTERVIEW_DEFAULT_QUESTION_COUNT: int = 6    # 默认问题数（夹到 3-10）
    AI_INTERVIEW_MAX_QUESTION_COUNT: int = 10
    AI_INTERVIEW_DEFAULT_DURATION_MINUTES: int = 30
    AI_INTERVIEW_LINK_TTL_HOURS: int = 72           # 面试链接有效期（小时）
    AI_INTERVIEW_LLM_PLAN: bool = True              # 是否用大模型个性化问题（失败自动规则降级）
    AI_INTERVIEW_LLM_FEEDBACK: bool = True          # 是否用大模型润色评语（分数仍由规则引擎给出）
    AI_INTERVIEW_LLM_TIMEOUT_SECONDS: int = 45      # 单次模型调用超时（秒）
    AI_INTERVIEW_ANSWER_MAX_CHARS: int = 4000       # 单题回答最大长度（防滥用）

    # ------------------------------------------- ComfyUI 本地数字人（[Agent组] AI 面试视频合成）
    # 用户在 ComfyUI（默认 http://127.0.0.1:8188）里搭好数字人工作流（MuseTalk / SadTalker /
    # AnimateDiff + 音频驱动），导出 API 格式 JSON，通过以下配置接入平台。
    # 未配置时，数字人视频生成自动降级为现有「照片 + 唇形同步」方案，面试不中断。
    COMFYUI_BASE_URL: str = ""               # 如 http://127.0.0.1:8188，空表示未启用 ComfyUI 数字人
    COMFYUI_WORKFLOW: str = ""               # 工作流 API JSON 字符串，或指向 workflow.json 的文件路径
    COMFYUI_TEXT_NODE_ID: str = ""           # 负责输入文本的节点 ID；留空则自动寻找含 "text" 字段的节点
    COMFYUI_TEXT_INPUT_KEY: str = "text"     # 文本输入字段名，默认 "text"
    COMFYUI_OUTPUT_NODE_ID: str = ""         # 负责保存视频的节点 ID；留空则自动从 outputs 提取首个视频
    COMFYUI_TIMEOUT_SECONDS: int = 300       # 单次 ComfyUI 生成超时（秒）
    COMFYUI_POLL_INTERVAL_SECONDS: float = 2 # 轮询历史间隔（秒）
    DIGITAL_HUMAN_PROVIDER: str = "comfyui"  # 数字人供应商：comfyui（预留 heygen/did 等扩展）

    # ------------------------------------------------------------------ 校验器
    # 说明：DIFY_API_KEY 现为 str 语义（见上方注释），原先的 _parse_dify_keys
    #       「把空字符串解析成 {}」校验器会把 dict 交给 str 字段导致启动即报错，已移除。

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
        """MySQL 异步驱动 DSN（业务运行时使用）。

        DB_DRIVER=sqlite 时返回单文件 SQLite DSN（本地开发/演示回退，
        见 DB_DRIVER 注释）；此时 MySQL 连接参数全部被忽略。
        """
        if self.DB_DRIVER == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def is_sqlite(self) -> bool:
        """业务主库是否运行在 SQLite 回退模式（本地开发/演示专用）。"""
        return self.DB_DRIVER == "sqlite"

    @property
    def sqlite_path(self) -> str:
        """SQLite 库文件绝对路径（相对路径一律相对项目根目录解析）。"""
        import os as _os

        path = _os.path.expanduser(self.DB_SQLITE_FILE)
        if not _os.path.isabs(path):
            path = _os.path.join(BASE_DIR, path)
        return path


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

    @property
    def is_test(self) -> bool:
        """是否为测试环境（APP_ENV=test）。

        测试环境下数据库引擎改用 NullPool：pytest-asyncio 为每个用例创建独立事件循环，
        而 QueuePool 会把连接缓存下来复用，连接仍绑定在「上一个已关闭的循环」上，
        下一次 checkout 触发 pool_pre_ping 时就会往已销毁的 transport 写数据，
        Windows 下表现为 `AttributeError: 'NoneType' object has no attribute 'send'`。
        NullPool 不复用连接，从根上避免该问题。
        """
        return self.APP_ENV == "test"

    def database_summary(self) -> dict[str, str]:
        """数据库连接摘要（脱敏，供日志 / 自检接口展示）。"""
        return {
            "mysql": (
                f"sqlite://{self.sqlite_path}"
                if self.is_sqlite
                else f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            ),
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
        """文件存储摘要（脱敏，不含密钥），供 /system/info 展示。[架构组合并]"""
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
        """创建运行期需要的目录（日志、上传、SQLite 库文件目录）。"""
        import os as _os

        if self.is_sqlite:
            _os.makedirs(_os.path.dirname(self.sqlite_path) or ".", exist_ok=True)
        for path in (self.LOG_DIR, self.UPLOAD_DIR):
            if path:
                os.makedirs(path, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """配置单例，便于测试时 `get_settings.cache_clear()` 后重新加载。"""
    return Settings()


settings: Settings = get_settings()