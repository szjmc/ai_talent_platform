"""
模块：app/services/__init__.py
职责：业务服务层出口。

标签：[架构组] [P1]（业务服务层出口与约定）

已实现：AuthService / UserService / OrganizationService
待实现：position / talent / resume / assessment / training / matching /
        knowledge / analytics / agent / file / audit（每个文件开头有方法清单）

约定：Service 接收 AsyncSession，返回 ORM 对象或 PageResult，不处理 HTTP。
"""
