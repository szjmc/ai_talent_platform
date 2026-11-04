"""
模块：app/ai/agents/matching_agent.py
职责：人岗匹配 Agent。

标签：[Agent组] [P1]  依赖：[架构组] BaseAgent + matching_score 技能（人岗匹配解释）

============================ 实现思路与代码要点 ============================
执行流程：


    接收 position_id + 候选人才列表
      -> 硬性条件过滤（学历 / 年限 / 证书）
      -> 软性能力向量相似度打分
      -> 加权总分（权重可配：技能 0.4 / 经验 0.3 / 学历 0.2 / 素质 0.1）
      -> LLM 生成「匹配解释」（必须引用真实字段，禁止编造）
      -> 结果写入 matching_records（按 batch_id 归组）



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
