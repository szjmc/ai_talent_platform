"""
模块：app/ai/agents/resume_agent.py
职责：简历解析 Agent。

============================ 实现思路与代码要点 ============================
执行流程：

    接收简历文本（或文件 ID）
      -> 文本清洗（去除页眉页脚、乱码）
      -> LLM 结构化抽取（姓名/学历/经历/技能/项目/年限）
      -> 校验与补全（手机号格式、时间区间合理性）
      -> 返回 JSON 并由 ResumeService 落库、绑定人才档案
      -> 记录 confidence 与 warnings 供人工复核




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
