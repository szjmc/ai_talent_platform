"""
模块：app/adapters/storage/local_storage_adapter.py
职责：本地文件存储适配器（开发 / 私有化部署默认方案）。

============================ 实现思路与代码要点 ============================
统一存储接口（建议先定义 base_storage.py 的 BaseStorage）：
    save(data: bytes, filename: str, subdir: str = "") -> str   返回相对路径
    read(path: str) -> bytes
    delete(path: str) -> bool
    exists(path: str) -> bool
    url(path: str) -> str                                      对外访问地址
实现要点：
- 落盘目录来自 settings.UPLOAD_DIR，按 `biz_type/yyyy/mm/` 分目录，避免单目录文件过多；
- 存储文件名 = uuid + 扩展名（utils/file_utils.safe_filename），防路径穿越；
- 删除采用「先标记后清理」，避免事务回滚后文件已丢；
- 后续接对象存储（OSS/MinIO）时新增 adapter 实现同一接口即可。
待实现清单：
    - [ ] BaseStorage 抽象类
    - [ ] LocalStorageAdapter（save/read/delete/exists/url）
    - [ ] 在 patterns.registry 注册为 "local"
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
