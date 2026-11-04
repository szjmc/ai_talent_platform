"""
模块：app/schemas/__init__.py
职责：数据校验层出口。

标签：[架构组] [P1]（数据校验层出口与命名规范）

命名规范：
    XxxCreate / XxxUpdate / XxxQuery / XxxResponse / XxxDetail
基类：
    BaseSchema  写模型（extra=forbid，严格入参）
    ORMModel    读模型（from_attributes=True）
    PageData[T] 分页 data 包装
已实现：common / auth / user / organization
待实现：position / talent / resume / assessment / training / matching /
        knowledge / analytics
"""
