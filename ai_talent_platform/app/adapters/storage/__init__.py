"""
模块：app/adapters/storage/__init__.py
职责：存储适配包入口（BaseStorage 契约 + 本地 / MinIO / 阿里云 OSS / S3 实现）。

标签：[架构组] [P1]

已实现后端：
    local_storage_adapter.py  本地磁盘（默认）
    s3_storage_adapter.py     通用 S3 兼容（SigV4）
    minio_adapter.py          MinIO（S3 协议 + path-style）
    oss_adapter.py            阿里云 OSS（Signature V1）
    object_storage_adapter.py 对象存储公共 CRUD 语义
    storage_paths.py          对象键规划与上传校验
"""
