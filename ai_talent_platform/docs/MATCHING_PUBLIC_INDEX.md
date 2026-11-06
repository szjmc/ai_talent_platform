# 岗位匹配公共接入索引

在IDE全局搜索：`[岗位匹配接入]`。
标记说明使用关系，不代表公共代码归岗位Agent独占，也不判断历史作者。

|文件|定位内容|
|---|---|
|app/config/settings.py|JD解析、匹配解释两个Dify Key|
|app/adapters/dify/dify_adapter.py|按工作流选择Key，兼容原调用|
|app/routers/router.py|岗位画像、匹配路由注册|
|app/core/permissions.py|匹配读/执行权限；下方角色表需一起核对|
|app/models/__init__.py|匹配/预警模型加载；批次模型由matching模块注册|
|app/config/prompt_config.py|匹配Agent总结模板|
|app/patterns/strategy.py|硬过滤、软评分、混合策略|
|app/repositories/position_repository.py|get_by_ids批量读岗位|
|app/repositories/talent_repository.py|list_candidates状态过滤|
|app/schemas/position.py|JobProfileParsed画像契约|

合并规则：公共文件按字段、方法、注册项合并，保留其他组内容，不整文件覆盖。
LLMManager、Embedding、Milvus等已有公共改动仍需架构组审查，未在本次注释中归属为岗位专有代码。
此次仅新增注释及本索引；Python文件AST逐一核对相同，业务逻辑未变。
