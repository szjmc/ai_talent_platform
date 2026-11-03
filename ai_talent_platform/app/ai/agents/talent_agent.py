"""
模块：app/ai/agents/talent_agent.py
职责：人才分析 Agent（生成人才画像）。

============================ 实现思路与代码要点 ============================
执行流程：
    接收 talent_id
      -> TalentService 读取人才档案 + 简历 + 测评 + 培训记录
      -> 组装 Prompt（config/prompt_config 或 ai/prompts）
      -> LLM 输出结构化画像（维度分 / 标签 / 优势 / 短板 / 建议岗位 / 潜力）
      -> Pydantic 校验输出，字段缺失或类型错误时重试或降级
      -> 写入 talent_portraits 表
      -> 返回画像摘要





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
