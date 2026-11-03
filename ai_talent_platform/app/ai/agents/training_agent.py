"""
模块：app/ai/agents/training_agent.py
职责：培训推荐 Agent。

============================ 实现思路与代码要点 ============================
执行流程：




    接收 talent_id（可选 position_id）
      -> 读取能力缺口（测评结果）与岗位能力模型
      -> 检索课程库（向量检索 + 规则过滤）
      -> LLM 生成个性化培训方案（课程 / 周期 / 考核节点）+ 推荐理由
      -> 写入 training_plans

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
