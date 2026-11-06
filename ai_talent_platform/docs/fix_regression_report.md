# 故障修复与回归（2026-11-05）

本报告为function_check_report.md之后的修复结果。没有创建、删除或修改数据库表结构；真实回归使用独立合成测试行并清理。

## 修复

- 简历模型请求附完整JSON Schema与JSON对象输出约束。仅将空数组/null规范化，结构不合法时反馈字段位置和类型，最多3次模型请求；不记录原始个人字段。
- S3/MinIO GET/HEAD/固定键PUT/DELETE对连接错误、408/429/5xx做最多3次幂等重试；认证与参数错误不重试。服务端502根因不在本次客户端修复内。
- BM25快照不可读或不可写时回源当前已授权的MySQL切片，不放宽权限；恢复扫描单条日志读取失败不阻断其他任务，错误状态回写失败仍保留pending。
- 技能knowledge_search通过可信上下文加载当前用户，再以真实会话实例化Retriever；修复同文档多切片去重键。
- A/B合并入口复用KnowledgeService、FileService、EmbeddingService、已有MySQL元数据和补偿流程，不再调用不存在的单例或Mock仓库。/files/upload、/ingestion/trigger、/ingestion/embed_query、/ingestion/chunks/fetch、/ingestion/delete均挂载并鉴权。
- 兼容trigger的file_id必须是MySQL文件中心ID，桶与对象键须匹配元数据；不再接受游离UUID文件作为已入库身份。切片拉取经权限过滤和有界分页；删除202受理，先提交逻辑删除，恢复任务后清向量，回滚不误删有效文档。
- 去除Settings重复字段声明、统一A/B Schema；MinIO SDK读流在finally释放连接。旧test2answer拼写文件改为兼容导出，不挂载重复路由。
- 修复测试中的独立配置隔离、UTF-16配置读取及已实现模块占位断言；补全模块标签，没有删掉测试或全局忽略失败。

## 验证

105项所选pytest通过（--noconftest；架构/标签/ORM/Skills/存储LLM适配/路由守门/4项故障注入）。没有运行原数据库自动建库及drop_all夹具。

真实服务回归全部返回0：scripts.smoke_resume_http --real-model、scripts.smoke_rag_durable、scripts.smoke_text2answer、scripts.smoke_ingestion_bridge。验证真实DeepSeek解析、失败状态、并发重复上传、pending恢复、成功提交保留、恢复幂等、混合检索重排、问答与多轮历史权限，以及A/B兼容入口上传/触发/拉取/删除。

仍有限制：服务器MinIO502根因待运维排查；并非所有业务模块都已实现；生产压力与准确率指标未验收；C端Webhook未配置和联调；简历后台仍为进程内任务。BM25降级可保持当前检索，但MinIO持续不可用时无法承诺新文件上传或持久化日志可用。
