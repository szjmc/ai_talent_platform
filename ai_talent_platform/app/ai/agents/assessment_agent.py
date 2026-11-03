"""
模块：app/ai/agents/assessment_agent.py
职责：测评分析 Agent。

标签：[Agent组] [P2]  依赖：[架构组] BaseAgent（测评报告生成）

============================ 实现思路与代码要点 ============================
执行流程：



    接收 assessment_id
      -> 汇总答题与规则得分
      -> LLM 分析能力结构、短板、等级、提升建议
      -> 生成雷达维度分与报告 JSON
      -> 写入 assessment_results


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
