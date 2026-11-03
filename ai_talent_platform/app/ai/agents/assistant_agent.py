"""
模块：app/ai/agents/assistant_agent.py
职责：综合问答 Agent（RAG 入口）。

============================ 实现思路与代码要点 ============================
执行流程：





    接收用户问题 + 会话历史
      -> 意图识别（政策问答 / 数据问数 / 人才查询 / 其他）
      -> 知识库 RAG 检索（权限过滤）或转交 NL2SQL / 业务 Agent
      -> 组装上下文 -> LLM -> 生成答案 + citations
      -> 保存会话消息与 token 统计
注意事项：
- 输入输出都要有 Pydantic 结构定义（放 schemas/ 下对应文件）；
- 提示词必须版本化，并在结果中记录 prompt_version / model_name；
- 调用业务数据只能通过 Service，禁止直连数据库。
待实现清单：
    - [ ] 继承 BaseAgent，实现 build_prompt / parse_output / persist
    - [ ] 在 agent_registry 注册
    - [ ] 补充单元测试（mock LLM 返回固定 JSON）
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
