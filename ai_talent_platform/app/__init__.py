"""AI 数字化人才平台 —— 应用包。

分层约定（依赖方向自上而下，禁止反向 import）：

    routers/     接口层：收请求、校验入参、调用 Service、返回统一响应
    schemas/     数据校验层：Pydantic 请求/响应模型
    services/    业务层：业务规则、流程编排、事务边界
    repositories/ 数据访问层：只做数据库读写，不含业务判断
    models/       ORM 映射层：SQLAlchemy 表结构
    ai/          AI 能力层：Agent / RAG / Workflow 编排
    adapters/    外部适配层：大模型、Dify、Milvus 等第三方能力
    infrastructure/ 基础设施层：MySQL/Redis/向量库连接与资源管理
    core/        公共能力层：应用创建、异常、响应、认证、依赖注入
    config/      配置层：环境变量、日志、提示词
"""

__version__ = "1.0.0"
