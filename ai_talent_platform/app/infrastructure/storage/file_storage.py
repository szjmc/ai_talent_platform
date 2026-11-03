"""
模块：app/infrastructure/storage/file_storage.py
职责：文件存储底层能力（目录规划、落盘、读取、删除）。

============================ 实现思路与代码要点 ============================
职责边界：
    infrastructure/storage/file_storage.py -> 与「本机文件系统」打交道（读写字节、路径规划）
    adapters/storage/local_storage_adapter.py -> 把上述能力包装成统一的存储接口
    services/file_service.py -> 业务逻辑（校验、元数据、权限）

本地存储约定：
    {UPLOAD_DIR}/{biz_type}/{yyyy}/{mm}/{uuid}.{ext}
    - 文件名必须重命名，禁止使用用户原始文件名落盘；
    - 删除采用「逻辑删除 + 延迟物理删除」，避免事务回滚导致文件丢失。

待实现清单：
    - [ ] build_path(biz_type, filename) -> 绝对路径（自动建目录）
    - [ ] write_bytes(path, data) / read_bytes(path) / remove(path)
    - [ ] get_size(path) / exists(path)
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
