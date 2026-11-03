"""
模块：app/schemas/resume.py
职责：简历上传与解析相关结构。

标签：[业务组] [P1]

============================ 实现思路与代码要点 ============================
需要定义的 Schema：
1. ResumeUploadResponse   file_id、resume_id、file_name、file_size、status="uploaded"
2. ResumeParseRequest     resume_id、force(是否重新解析)、talent_id(可空，解析后绑定)
3. ResumeParseResult      resume_id、status(parsing/success/failed)、
                          parsed(结构化内容：basic_info/education/work_experience/projects/skills)、
                          accuracy、warnings: list[str]、parsed_at
4. ResumeResponse         id、file_name、file_type、file_size、status、talent_id、created_at
5. ResumeQuery            keyword、status、talent_id
实现要点：
- 上传接口用 UploadFile，校验扩展名（settings.allowed_upload_extensions）与大小；
- 解析是耗时任务：接口立即返回 task_id（或 resume_id + status=parsing），
  真实解析在 ai/workflows/resume_workflow.py 中异步执行，结果写回 resume 表；
- parsed 结构必须与 models/resume.py 中 JSON 字段定义一一对应。
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现本文件的 Pydantic 模型。
