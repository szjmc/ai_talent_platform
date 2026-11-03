"""
模块：app/routers/__init__.py
职责：接口层出口。

标签：[架构组] [P1]（接口层出口与实现状态清单）

已实现：
    auth_router          认证（注册/登录/刷新/当前用户/改密/退出）
    user_router          用户管理
    organization_router  组织架构
    router.py            总路由（api_router，统一前缀 /api/v1）
    _stub.py             未实现模块的占位路由工厂

待实现（每个文件开头有接口清单）：
    position / talent / resume / assessment / training / matching /
    knowledge / analytics / agent / file
"""
