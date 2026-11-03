"""
模块：app/adapters/__init__.py
职责：外部能力适配层出口。

本层只做「第三方产品 -> 平台统一接口」的翻译，不含业务规则。
目标结构：
    llm/         大模型适配（OpenAI / 通义千问 / Ollama）
    dify/        Dify 应用与工作流适配
    nl2sql/      Vanna 适配 + SQL 安全校验
    vector_store/ Milvus 适配（统一向量库接口）
    storage/     本地 / 对象存储适配
"""
