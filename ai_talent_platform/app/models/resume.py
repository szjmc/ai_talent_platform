"""
模块：app/models/resume.py
职责：简历文件表 + 简历解析结果表。

============================ 实现思路与代码要点 ============================
表设计：
1. resumes
   - talent_id(可空，解析后回填)、file_id(->files)、file_name、file_type、file_size、
     file_hash(去重)、status(uploaded/parsing/success/failed)、
     parsed_data(JSON，结构化简历)、parse_error、parsed_at、accuracy
2. resume_experiences（可选拆表，便于统计分析）
   - resume_id、company、title、start_date、end_date、description
关键思路：
- file_hash（sha256）用于秒传与重复简历识别；
- parsed_data 的 JSON 结构与 schemas/resume.py 中 ResumeParseResult.parsed 一致；
- 解析失败保留 parse_error，便于人工复核与 Prompt 迭代。
待实现清单：
- [ ] 模型字段与索引（talent_id、status、file_hash 唯一）
- [ ] ResumeRepository：get_by_hash、list_by_talent、update_parse_result
- [ ] ResumeService：上传 -> 存文件 -> 创建 resume 记录 -> 触发 AI 工作流
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 Resume 模型，并在 app/models/__init__.py 注册。
