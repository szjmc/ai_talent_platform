"""
模块：app/models/file.py
职责：文件记录表（统一管理上传文件元数据）。

============================ 实现思路与代码要点 ============================
表设计：files
- file_name(原始名)、stored_name(磁盘名/UUID)、file_path、file_size、content_type、
  extension、file_hash(sha256，秒传与去重)、storage_type(local/oss)、
  biz_type(resume/knowledge/avatar/export)、biz_id(关联业务 ID)、
  uploader_id、is_public、download_count
关键思路：
- 文件本体存磁盘（adapters/storage/local_storage_adapter.py），表里只存元数据；
- file_hash 唯一（可选）实现秒传：相同文件不重复落盘；
- 下载接口必须校验权限（biz_type + 归属人 + 角色）。
待实现清单：
- [ ] 模型字段与索引（file_hash、biz_type + biz_id）
- [ ] FileRepository：get_by_hash、list_by_biz、incr_download
- [ ] FileService：保存、校验、下载鉴权、清理孤儿文件
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 File 模型，并在 app/models/__init__.py 注册。
