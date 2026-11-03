"""
模块：app/ai/utils/output_parser.py
职责：AI 输出解析（JSON 容错 + Schema 校验）。

============================ 实现思路与代码要点 ============================
现实问题：模型经常返回 ```json ... ``` 、前后夹杂解释文字、字段缺失。
处理策略：
1. 剥离 Markdown 代码块与首尾噪声；
2. 定位第一个 { 与最后一个 } 尝试 json.loads；
3. 失败时尝试修复常见问题（尾逗号、单引号、中文引号）；
4. 用 Pydantic 模型校验，字段缺失给默认值或标记 warnings；
5. 仍失败则抛 core.exceptions.AIError，由上层决定重试。
对外 API：
    parse_json(text: str) -> dict
    parse_model(text: str, schema: type[BaseModel]) -> BaseModel
待实现清单：
    - [ ] parse_json / parse_model
    - [ ] 修复策略与单元测试
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
