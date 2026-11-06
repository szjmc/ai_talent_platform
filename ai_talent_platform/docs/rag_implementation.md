# RAG 实现与流程说明

沿用现有分层与 ORM 表。总路由替换知识库占位注册，FileService 增加上传和内部读取方法。

## 入库流程

上传 file + category → FileService 校验大小和分类 → MinIO knowledge/category/UUID 保存原件 → MySQL 创建 File 和 KnowledgeDoc(indexing) → 临时文件解析 → LangChain 清洗及重叠分块 → 统一模型门面批量 Embedding → MySQL 保存切片分配主键 → Milvus 写入主键和向量 → indexed；异常则 failed 并记录错误。

## 检索流程

当前用户权限 → MySQL 获取可访问的 indexed 文档 → 分类过滤 → 问题向量化 → Milvus 白名单召回 + MySQL 关键词召回 → RRF 融合 → 回表 → 返回文件ID、页码、原文、得分。

## 配置和接口

复用 .env.example：STORAGE_BACKEND=minio、MINIO_*、DB_*、MILVUS_ENABLED=true、MILVUS_URI、MILVUS_TOKEN、LLM_EMBED_PROVIDER、LLM_EMBED_MODEL。真实密钥不提交仓库。全私有部署需要把统一模型门面配置为内网模型端点。

POST /api/v1/knowledge/docs/upload：multipart file、category，需要 knowledge:manage；当前请求内等待索引。

POST /api/v1/knowledge/retrieve：JSON question、category、top_k、mode、score_threshold，需要 knowledge:read。mode=semantic/keyword/hybrid。

分类是对象前缀，例如 knowledge/技术文档/UUID.pdf、knowledge/培训文档/UUID.docx；MySQL category 保存对应分类。上传默认 public，使用已有平台权限。

score 是 RRF 分数，不是准确率；semantic_score 是余弦分数。score_threshold 只过滤语义支路。关键词使用原文包含匹配。Retriever 支持注入异步 reranker，默认未接模型重排。

## 尚未完成的需求

当前支持文本型 PDF、docx、xlsx、UTF-8 TXT/Markdown。图片及扫描件 OCR、旧版 doc/xls、Embedding 语义分块尚未实现。Word 无稳定页码，Excel sheet 暂未存入现有切片表。

尚无持久化后台队列、失败重试、列表和删除接口。请求提交失败可能留下对象和向量孤儿，需要补偿清理；MySQL indexed 白名单避免这些向量被正常召回。并发首次建集合需部署前初始化。

问答和多轮会话不在本次范围。准确率≥95% 与500ms目标必须通过标注集和真实压测验证，目前不作达标声明。

## 验证

Python compileall 语法检查通过。项目 .venv 缺少 FastAPI 等依赖，安装因代理不可连接失败；接口运行和 MySQL/MinIO/Milvus 联调尚未验证。原 requirements.txt 为 UTF-16，未覆盖原依赖清单。

## db312 环境验证更新

已使用 C:/Users/Windows/miniconda3/envs/db312/python.exe。接口及总路由导入、TXT 解析、重叠分块、来源元数据保留、空问题校验通过。缺少 pytest、pdfplumber、python-docx、openpyxl。MySQL 3306 端口可达（未验证账号和库）；本地 MinIO 9000、Milvus 19530 不可达。当前配置 STORAGE_BACKEND=local、MILVUS_ENABLED=False，真实入库联调仍待服务配置。


## 远程 Milvus 验证

本地 .env 已启用 MILVUS_ENABLED，并配置用户提供的远程 URI。db312 通过项目连接管理模块的 health_check 检查。远端现有 talent_chunks、knowledge_chunks、assessment_chunks；本实现使用 knowledge_vectors，未读写现有集合数据，也未创建集合。后续首次入库会按真实 Embedding 维度创建 knowledge_vectors，不直接复用未知模型与 Schema 的旧集合。MinIO 与 Embedding 配置仍需完成后才能全链路联调。


## MinIO 与 SiliconFlow 联通验证

本地 .env 已配置远程 MinIO 和 SiliconFlow；密钥仅存在于被 Git 忽略的 .env。SiliconFlow 使用现有 OpenAI 协议适配器：SILICONFLOW_API_KEY 对应 OPENAI_API_KEY，SILICONFLOW_BASE_URL 对应 OPENAI_BASE_URL，EMBEDDING_MODEL_NAME 对应 LLM_EMBED_MODEL，LLM_EMBED_PROVIDER=openai。未修改对话模型供应商。EMBEDDING_DIM 与 EMBEDDING_BATCH_SIZE 已纳入 Settings，EmbeddingService 校验实际维度并按配置批量调用。

MinIO ai-talent 桶健康检查 HTTP 200；使用非业务测试文本调用统一 Embedding 门面，返回 1 条 1024 维向量。未上传企业文档或写入远程 Milvus。此次验证不代表 MySQL 元数据与三存储完整事务链路已验收。SiliconFlow 为外部 API，当前部署不属于模型完全离线部署。


## 实际存储联调结果

使用 db312 执行 python -m scripts.smoke_rag_storage：MinIO 写入/读回、1024维 Embedding、Milvus 写入/查询、doc_id 范围过滤、向量删除全部通过；测试对象与 UUID 隔离集合已清理。语义命中余弦分数约0.665，仅表示单条测试命中，不作为准确率指标。

python -m scripts.smoke_rag_service 已准备好完整业务链路检查和测试数据回滚清理。本机 MySQL 使用用户提供的 admin 账号仍返回1045，等待确认数据库主机与端口，尚未执行元数据写入及关键词/混合检索验收。


## 远程 MySQL 完整 Service 联调

更新用户提供的 DB_HOST 后，db312 执行 python -m scripts.smoke_rag_service 通过：文件上传、MinIO 保存、MySQL 文档/切片写入、1024维向量写入 Milvus、indexed 状态、semantic/keyword/hybrid 三种检索均成功。数据库测试事务已回滚，测试文档对应的向量和 MinIO 对象已删除。没有创建测试账号；没有可用超级管理员时，脚本仅使用内存中的普通员工身份测试 public 文档，不代表 JWT/HTTP 权限已验收。

现有 knowledge_chunks 是字符串 chunk_id、file_id、category、content、source、page_number、embedding(1024) 的固定结构，enable_dynamic_field=False；与当前整数 id/vector/doc_id 适配器不兼容。因此当前实现仍使用 knowledge_vectors，未修改现有 knowledge_chunks。MILVUS_COLLECTION_* 和 Webhook 配置尚未接入。CHUNK_SIZE=500、CHUNK_OVERLAP=100 已纳入 Settings 并用于默认分块。


## 第一步：集合兼容修复（当前有效实现）

此前 knowledge_vectors 的说明已被本节替代。MilvusAdapter 现在默认读取 Settings.MILVUS_COLLECTION_KNOWLEDGE=knowledge_chunks，直接兼容既有固定 Schema。业务请求只验证集合，不创建、重建或删除共享集合；校验字段类型、主键配置和向量维度。

字段映射：MySQL chunk.id 生成字符串 chunk_id=ragdoc-{doc.id}-chunk-{chunk.id}；file_id 存真实文件ID；category/content/source/page_number 来自原文元数据；embedding 存模型向量。命名空间前缀用于文档白名单过滤和精确清理，检索后恢复整数 chunk.id 回表。其他命名规则的历史数据不会自动纳入当前检索，需通过显式迁移及元数据关联后接入。现有 knowledge_vectors 也未自动迁移或删除。

验证：db312 执行 scripts.smoke_rag_service 完成真实上传、MySQL切片、固定集合写入及 semantic/keyword/hybrid 检索。scripts.smoke_rag_storage 完成共享集合内测试记录写入、查询、文档范围过滤、删除与删除后不可召回检查；MinIO测试对象已清理，业务测试事务已回滚。未删除或重建远端已有集合。compileall 与 git diff --check 通过。

后续第二步处理失败重试、重复入库与跨存储补偿；本步骤未声称解决跨存储事务可靠性、历史数据迁移、HTTP鉴权及准确率验收。


## 第二步：入库可靠性阶段进展

新增 POST /api/v1/knowledge/docs/{doc_id}/retry，需 knowledge:manage 且为文档创建人或超管。失败重试先锁文档行，清除旧向量和切片，再从原件重建；indexed 文档直接返回，正在索引时拒绝重复触发。去重范围为同一创建人、分类、文件 SHA256。

索引置于数据库保存点内，向量部分写入后失败则撤销切片、标记 failed，保留原件供重试。向量清理失败记录 cleanup_pending 和日志。接口依赖在响应发送前提交；事务失败后回滚，再清理本请求新建的对象和向量。

故障注入脚本 python -m scripts.smoke_rag_reliability 通过：远程向量写入后人为抛错、失败切片回滚、重试成功、顺序重复上传复用、事务回滚后 MinIO/Milvus 补偿。脚本清理本次测试资源。

仍未完成：并发首次上传缺少数据库唯一约束或分布式锁，当前仅保证顺序去重；进程崩溃/断电后的持久化补偿队列未实现；外部服务持续不可用时清理仅记录日志，仍需恢复任务。此阶段不应声称已完成跨存储强一致性或生产级可靠性验收。


## 无数据库结构变更的并发与持久化恢复（当前方案）

已移除未启用的补偿表声明、建表脚本和DDL。没有创建或修改数据库表。补偿日志为 MinIO ai-talent 桶下 _rag_ingest_jobs/v1/{UUID}.json；先记准确对象键，再 PUT 原件；先记 doc_id，再写 Milvus。任务从 pending 到 done，日志确认失败保留 pending。

MySQL GET_LOCK 在独立连接上持有同用户/分类/内容哈希锁，业务提交确认后释放；超时返回冲突。服务调用者必须在提交后 finish_jobs(True)，失败或不确定提交后 finish_jobs(False)。HTTP 依赖已接入。多实例应共用同一个 MySQL 主节点，不能把命名锁分散在不同实例。

知识路由生命周期启动恢复 worker，每60秒扫描；可手动 python -m scripts.recover_rag_jobs。恢复只处理能够取得命名锁的任务；数据库已 indexed 则保留文件与向量；未提交则清理对象与向量；已有失败文档保留原件并标记可重试。恢复异常保留 pending，下轮重试。不要对该专用前缀配置提前自动过期。

本地 .env 已启用 RAG_DURABLE_INGEST_ENABLED=true。使用 db312 执行 python -m scripts.smoke_rag_durable，两个并发上传复用同一文档、pending中断恢复、已提交数据保留、恢复幂等全部通过。中断测试为持久化pending后断开锁连接的模拟，并非强杀进程测试。一次验证因45秒总超时中止，后用120秒上限完整通过并清理已知测试残留。

边界：这不是三存储分布式事务；MySQL/MinIO恢复后最终一致，持续网络故障时任务保留。历史无日志孤儿不自动删除；完成日志目前保留，需要后续按审计周期归档。


## HTTP 认证与权限验收

脚本 python -m scripts.smoke_rag_http 使用真实 FastAPI 应用、ASGITransport、中间件、异常处理、JWT 和远程数据库，不覆盖认证依赖；不运行 lifespan，避免现有开发模式自动建表。仅创建独立标记的测试行，未改变表结构，账号/部门/文档/对象/向量测试数据已清理。

通过项：真实账号密码登录；匿名/无效令牌/刷新令牌误用为访问令牌返回401；无权限账号检索403；普通员工上传/重试403；部门内外过滤；多个 allowed_roles 的成员判断；管理员上传并完成索引；员工语义检索命中文档；文档创建人重试返回200。

修复角色数组过滤为 JSON_CONTAINS(allowed_roles, JSON_QUOTE(role))，避免 SQLAlchemy 通用 JSON contains 编译成字符串 LIKE 导致多角色匹配错误。

本次为进程内 HTTP 协议层验收，未覆盖反向代理、浏览器CORS、TLS或部署端口。单次远程语义检索约1.1秒、同步上传约10秒，不能据此宣称满足500ms/1秒指标；持久化后台索引队列和性能优化仍待后续处理。


## 多格式与本地OCR处理（当前实现）

新增 requirements-rag.txt 增量依赖，db312 已安装。文本PDF优先提取原文；无文本页用 PDFium 渲染后交本地 RapidOCR，支持文字/扫描页混排并保留实际页码。图片支持 png/jpg/jpeg/bmp/tif/tiff，TIFF 多帧逐帧识别；空白和低置信度无有效文本则返回可解释错误。OCR 图片不发送外部服务；提取后的文本仍按已配置 SiliconFlow Embedding 发送向量化。

Word 使用 iter_inner_content 保留正文段落与表格顺序，不伪造页码；不保证页眉页脚、浮动文本框和嵌套表格完整提取。Excel 每行一个 Document，保留 sheet/row 元数据；切片正文附带工作表与行号，因此不需要修改 MySQL 表或 Milvus Schema。公式读取缓存值，未缓存公式不执行计算。旧版 doc/xls 仍需先转换。文字页中单独嵌入的图片目前不额外OCR。

脚本 python -m scripts.smoke_rag_documents 通过：Word段落表格顺序、Excel多工作表来源、中文图片OCR、文字+扫描混排PDF页码、空白图拒绝。样例均在临时目录生成并自动清理；此次测试是解析层验证，不代表复杂真实简历OCR准确率达标。数据库结构未变更。

OCR_ENABLED、OCR_MIN_CONFIDENCE、OCR_MAX_PIXELS、OCR_PDF_SCALE、DOCUMENT_MAX_PAGES 可配置，防止大图片/大量页数无限处理。下一阶段仍需补语义分块与检索策略优化。


## 语义分块与中文检索策略

新增 CHUNK_STRATEGY=recursive/semantic；默认递归切分保持兼容。semantic 将同一来源内的句子向量化，根据相邻句余弦相似度和长度上限断块；不跨页/工作表合并；语义断点不混入上一主题，长度断点尽量保留重叠。使用统一 EmbeddingService，开启后会增加向量调用量，既有文档需重建索引才生效。阈值 SEMANTIC_SPLIT_THRESHOLD 默认0.65，需根据语料调参。

keyword 模式已从整句包含匹配改为 jieba 分词+BM25Plus（delta=0），在 MySQL 权限过滤后的切片中计算，过滤无查询词命中的片段。hybrid 与语义召回使用 RRF 融合。BM25 当前按请求读取授权语料并构建内存索引，适合初期联调；数据量扩大前必须做持久索引/增量更新，不能宣称满足海量语料500ms指标。

请求增加 use_rerank=false；true 时默认使用本地 lexical_coverage 重排（查询词覆盖率，RRF得分作为次排序），返回 rerank_score/rerank_method。这是可用的词项重排，不是跨编码器神经网络模型；Retriever 仍可注入异步模型重排器。未擅自选择外部付费重排模型。Webhook BM25 增量同步仍未实现。

验证：scripts.smoke_rag_search 通过语义断点及来源保留、中文多词BM25召回、可选重排固定样例。语义断点测试使用确定性假向量，不作为真实模型语义质量指标。scripts.smoke_rag_http 真实路由回归通过登录、401/403、部门/角色隔离、上传、语义召回和重试，测试数据清理完成。数据库表结构未改动；准确率95%尚需标注集评测。


## 模型重排与BM25持久化增量快照（当前实现）

use_rerank=true 现使用真实 SiliconFlow /rerank 接口，模型由 RERANK_MODEL 配置；本地.env配置 BAAI/bge-reranker-v2-m3。RERANK_API_KEY 为空则复用 OPENAI_API_KEY，RERANK_BASE_URL 为空复用 OPENAI_BASE_URL。仅发送权限过滤后的候选正文；校验返回序号、重复项、完整性及有限分数，错误显式抛出，不冒充模型成功。官方协议 https://docs.siliconflow.cn/docs/api/rerank-post 。

BM25按文档 id/version/indexed_at/chunk_count 形成版本指纹，切片正文与分词结果持久化在MinIO _rag_bm25/v2/{doc_id}/{指纹}.json，进程内最多缓存128份快照。文档提交后更新快照，失败保留pending任务由恢复worker重试；历史文档首次检索时补建。检索只加载当前权限可见文档的当前版本；无需每次从MySQL读取全部正文或重新分词。BM25统计仍按授权语料在请求内组装以保证过滤先行，并非全量磁盘倒排搜索引擎，海量数据性能需单独压测。更新文档必须更新索引时间或版本；直接绕过Service改切片内容不受支持。旧版本对象暂保留，不参与当前检索，后续需归档策略。

脚本 scripts.smoke_rag_bm25_index 通过真实MinIO快照复用（清内存后不再回源）、版本变化仅更新该文档、空权限集合不召回及真实模型重排。scripts.smoke_rag_http 已更新为 hybrid+use_rerank=true，真实HTTP认证、401/403、部门/角色过滤、上传、混合模型重排及重试全通过，测试快照与其他测试数据已清理。未配置C端Webhook地址，当前内部自动增量更新，不向占位地址发送回调。未改变数据库结构。
