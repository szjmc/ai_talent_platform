# 简历模块使用与验收

此前 schemas/resume.py 只有设计注释，现已定义请求/响应模型并由真实接口使用。沿用既有 resumes、resume_experiences、files、talents 表，无数据库结构变更。

## 接口

- POST /api/v1/resumes/upload：multipart file，需 resume:upload；保存私有原件及简历记录；同一上传者重复内容复用记录，跨用户相同内容拒绝访问，不暴露已有简历ID。全局哈希唯一冲突返回409。
- GET /api/v1/resumes：分页，可筛 status、talent_id、keyword。
- GET /api/v1/resumes/{id}：文件信息和状态，不返回对象键。
- POST /api/v1/resumes/{id}/parse：JSON force=false，需 resume:parse，202受理，后台执行；force=true重解析，增加现有 version 字段。
- GET /api/v1/resumes/{id}/result：结构化内容、warnings、错误及解析时间。
- POST /api/v1/resumes/{id}/bind：JSON talent_id，需 resume:parse，绑定已经存在且可管理的人才档案；不会擅自新建人才。

## 流程与边界

上传校验 → FileService/MinIO保存 → Resume uploaded → 解析请求提交parsing → BackgroundTasks独立会话 → 复用Document Loader/OCR → 统一LLMManager结构化 → ParsedResume校验 → 工作经历拆表及JSON持久化 → success/failed。

管理员与超管可管理全部；其他角色只访问自己上传的简历，列表、详情、解析和绑定均执行归属检查。普通员工按现有平台权限无resume:upload/resume:parse。

解析工作流在 app/ai/workflows/resume_workflow.py，不自行实现供应商客户端。使用 purpose=resume_parse，对应 LLM_MODEL_MAP 可覆盖模型。必须提供可用的对话模型凭证；Embedding与重排模型不能作为简历抽取模型。准确率不伪造，accuracy暂为空。40000字符以上拒绝解析，缺失或不完整日期要求返回null及warnings。

后台任务目前为进程内BackgroundTasks，不具备持久队列或进程崩溃恢复保证；进程中断可能留下parsing状态。简历上传尚未复用知识库专属持久化补偿日志，不应声称已具备同等级的跨存储崩溃恢复。尚无删除/下载接口、自动创建人才、简历向量相似查重或历史解析版本归档；force增加版本号但不保存每个历史JSON副本。

## 验证

执行 db312 下 python -m scripts.smoke_resume_http。真实ASGI路由、登录JWT、MySQL与MinIO均参与，结构化LLM使用标明的测试替身。通过上传去重、跨用户隔离、后台结果落库、列表/结果查询、绑定不存在目标返回404、强制解析故障置failed且不返回旧成功结果。测试数据已清理。未运行应用自动建表生命周期。

真实对话模型配置尚待提供，真实简历抽取和成功绑定现有人才的业务联调尚未验收。不会把测试替身结果当成真实模型效果。


## DeepSeek 接入配置

用户选择DeepSeek后，本地.env已将LLM_PROVIDER设为deepseek，DEEPSEEK_BASE_URL=https://api.deepseek.com，模型与resume_parse用途映射设为deepseek-flash（按当前官方文档）。尚缺DEEPSEEK_API_KEY，未发送真实简历解析请求。Embedding仍使用SiliconFlow的BAAI/bge-large-zh-v1.5，重排配置不变。


## DeepSeek 真实模型联调结果

本地.env已填写用户提供的DeepSeek密钥，不在文档或代码中保存密钥。使用db312执行 python -m scripts.smoke_resume_http --real-model，合成简历通过真实DeepSeek结构化抽取并写入MySQL，Python/FastAPI技能断言通过；上传去重、跨用户隔离、列表/结果查询及故障状态回归同时通过。故障注入分支仍使用明确的异常替身，不当作真实模型失败。测试账号、简历、经历及MinIO原件已清理。数据库结构未改变。该验证仅为合成短简历，不代表复杂真实简历的抽取准确率验收。
