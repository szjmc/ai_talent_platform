"""
模块：app/infrastructure/vector/milvus.py
职责：Milvus 客户端连接管理（只负责连接，不做检索语义）。

标签：[RAG组] [P0]（被 milvus_adapter 依赖：连接管理 / 集合初始化 / 健康检查）

============================ 实现思路与代码要点 ============================
1. init_milvus() / get_milvus() / close_milvus()，由 lifespan 在 settings.MILVUS_ENABLED 时调用；
2. 依赖 pymilvus；未安装或未启用时 get_milvus() 返回 None，上层降级（跳过向量能力）；
3. 连接参数来自 settings.MILVUS_URI / MILVUS_TOKEN；
4. 集合创建、索引参数、检索语句都在 adapters/vector_store/milvus_adapter.py 中实现。

待实现清单：
    - [ ] init_milvus / get_milvus / close_milvus
    - [ ] health_check（list_collections 探测）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
