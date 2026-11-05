"""
模块：app/infrastructure/storage/__init__.py
职责：文件存储基础设施包入口（本地目录规划 / 原子写入 / S3 与 OSS 底层客户端）。

标签：[架构组] [P1]

组成：
    file_storage.py  本地文件系统读写与路径规划
    s3_client.py     S3 协议底层客户端（MinIO / AWS S3，SigV4 签名）
    oss_client.py    阿里云 OSS 底层客户端（Signature V1）
"""
