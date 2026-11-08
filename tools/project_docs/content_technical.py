from .docx_common import h,h2,h3,p,bullets,steps,table,code,note,sign,kv,blanks
from .content_data import *
from .content_management import S

def specs(f):
    out=[]; mysql=f['models']['MySQL']; pg=f['models']['PostgreSQL']; routes=f['routes']
    out.append(S("AITP-03-01","概要设计说明书（HLD）","03_设计",[
        h("设计目标与约束"),p("构建前后端分离、分层解耦、AI中台协同的企业级人才平台。强制技术栈与私有化边界来自原始需求；当前代码为FastAPI分层后端+React前端。设计目标不等同于已验收能力。"),
        h("总体架构"),table(["层","组件","职责","代码线索"],[["业务应用层","React PC/移动适配、候选人邀约页","用户交互、同源API、SSE消费","frontend/src/pages, App.tsx"],["服务接口层","FastAPI routers/schemas/core","校验、鉴权、统一响应、限流、trace","app/routers, app/schemas, app/core"],["业务服务层","services/*","业务规则、事务边界、跨模块编排","app/services"],["AI能力层","ai/agents, workflows, skills, rag, memory, core","Agent、RAG、Skills、NL2SQL桥接、记忆","app/ai"],["数据访问层","repositories/* + models/*","ORM CRUD、查询、元数据","app/repositories, app/models"],["外部适配层","adapters/llm,dify,nl2sql,vector_store,storage","协议转换、错误映射、降级","app/adapters"],["基础设施层","MySQL/PgSQL/Milvus/Redis/对象存储/调度","连接池、缓存、向量、文件、事件、定时","app/infrastructure"]]),
        code("Browser(React/Vite) --同源 /api/v1--> Gateway/FastAPI\n  -> Trace/RateLimit/JWT/RBAC/AIContext\n  -> Service -> Repository -> MySQL\n  -> AI dispatch -> Skills/Agents/Workflows -> Adapters -> LLM/Dify/Milvus/Vanna\n  -> PgSQL(只读分析) / Redis(记忆/限流/状态) / Object Storage(文件)\n  -> SSE/JSON + Audit/trace_id"),
        h("技术选型"),table(["领域","选型","理由 / 约束"],[["后端","FastAPI + SQLAlchemy 2 Async ORM","异步接口、自动文档、ORM；业务代码禁裸SQL"],["业务库","MySQL 8","OLTP主数据；当前ORM 38表"],["分析库","PostgreSQL 16","宽表/时序/只读分析；当前ORM 6表"],["向量","Milvus 2.4+","语义检索/RAG/匹配；权限前置过滤"],["缓存","Redis 7","会话记忆、状态、限流、配额；可降级"],["AI编排","LangChain + LangGraph","文档/检索/有状态工作流"],["问数","Vanna.ai + sqlglot","NL2SQL与AST安全校验"],["中台","Dify","模型/Prompt/工作流/知识库；双模回退"],["前端","React 19 + TypeScript + Vite + Ant Design + ECharts","当前代码基线；同源代理"]]),
        h("模块划分与归属"),table(["模块","主责组","关键接口前缀"],[["认证/用户/组织/文件/审计","架构组","/auth /users /organizations /files /audit"],["知识库/人才/简历/RAG","RAG组","/knowledge /talents /resumes /rag /text2answer"],["岗位/匹配/Agent/邀约","Agent组","/positions /matchings /agents /invites /exam"],["分析/测评","Vanna组","/analytics /assessments"],["培训/Dify","Dify组","/trainings /dify"],["系统探针","架构组","/health /health/ready /api/v1/system"]]),
        h("部署架构"),p("开发可用仓库docker-compose启动MySQL/PgSQL，可选Redis/MinIO；生产必须独立集群、密钥托管、明确CORS、关闭DEBUG、TLS网关、备份监控。前端浏览器只访问同源相对地址，由网关/Vite代理到后端，禁止浏览器直连localhost。"),table(["环境","拓扑要点","准入"],[["开发","单机compose+本地模型/Mock","默认口令仅限本地"],["测试","独立测试库+可切换外部依赖","夹具隔离、脱敏数据"],["预生产","与生产同构或批准差异","迁移/回滚/压测演练"],["生产","应用多实例+四库集群+对象存储+模型/中台+监控备份","安全基线、容量、HA、接管"]]),
        h("接口与数据总览"),bullets(f"静态源码端点线索{len(routes)}条（含1条WebSocket与探针；不等同于运行时OpenAPI或联调通过）。详见API Doc。","MySQL当前ORM 38表、PgSQL 6表；Milvus集合与Redis键空间见DBD。","统一响应{code,message,data,trace_id}；SSE事件start/message/citation/done/error。"),
        h("关键设计决策"),table(["决策","选择","替代 / 影响"],[["ADR-01","四库职责分离","单库简单但违背原需求且分析/向量性能受限"],["ADR-02","AI身份仅来自服务端AIContext","信任Prompt参数会导致越权，禁止"],["ADR-03","NL2SQL仅白名单只读SELECT+AST","自由SQL不可接受；例外必须校验"],["ADR-04","外部依赖可降级但如实标注","降级不能冒充目标AI能力"],["ADR-05","历史DDL仅作参考，生产走DBA迁移","create_all不补列，旧DDL可能漂移"]]),
        h("现状差异与风险"),table(["项","发现","处理"],[["文档漂移","旧SRS/README表数量与状态不一致","以当前元数据+RTM复核"],["装配导入","文档环境create_application因proctoring导入失败","完整环境复核，列为上线前阻断核验"],["外部能力","历史报告提示Dify/LLM/Milvus降级","真实联调与效果评测"],["性能安全","缺少当前基线实测","压测/安全/国产化报告"]]),sign(["架构负责人","各组负责人","安全 / DBA","项目经理"]),
    ]))
    out.append(S("AITP-03-02","详细设计说明书（LLD）","03_设计",[
        h("分层调用规则"),table(["层","允许","禁止"],[["Router","Schema校验、调用Service、统一响应","碰Repository/写SQL/写业务状态机"],["Service","本模块Repository、公开Service、AI dispatch、事务","接收Request/Response、跨组私有Repository、裸SQL"],["Agent/Workflow","Skills、公开Service、LLMManager、记忆","直接ORM/SQL、绕过AIContext"],["Skill","公开Service/Retriever/VannaAdapter","信任入参user_id/role/org"],["Repository","BaseRepository、ORM select/update","commit、业务规则、外部HTTP"],["Adapter","infrastructure、httpx、pymilvus、sqlglot","感知业务模型"]]),
        h("公共组件"),bullets("AIContext：请求级身份/角色/部门/trace/conversation；set/get/context_scope。","LLMManager：chat/stream/embed/generate，模型选择与配额；输出parse_json与ai_retry。","BaseSkill：name/description/parameters/required_permission/timeout；to_openai_tool。","BaseAgent：build_prompt/parse_output/persist/run/stream；注册表dispatch。","BaseRepository：通用异步CRUD；BaseModel含id/version/时间戳/软删除/操作人。"),
        h("核心时序设计"),h2("简历解析建档"),code("上传 -> 文件校验/hash -> 存储 -> 解析任务 -> extract/clean/structure/validate\n  -> 人工复核(可选) -> 查重 -> 建档/画像 -> 向量入库 -> 审计/补偿"),
        h2("人岗匹配"),code("JD解析 -> 画像确认(version) -> load_context -> hard_filter -> recall\n  -> score(weights) -> evidence_build -> explain(Dify/LLM/rule) -> evidence_verify\n  -> persist(batch/records) -> review/warnings"),
        h2("RAG知识问答"),code("文档草稿 -> 切片预览 -> 发布 -> embedding/index -> 权限前置检索\n  -> 混合召回/RRF/rerank -> 阈值判断 -> 有依据生成+citations / 无依据拒答"),
        h2("NL2SQL"),code("question -> Vanna生成SQL -> sqlglot AST校验(单条SELECT/WITH/白名单)\n  -> 注入数据范围 + LIMIT -> PgSQL只读+statement_timeout -> chart/insight -> report"),
        h2("培训闭环"),code("测评/岗位标准 -> gap诊断 -> 计划排期 -> 课程匹配 -> 落库推送\n  -> 本人进度上报/防刷 -> 滞后扫描 -> 考核复测 -> 成长台账"),
        h("异常、重试与幂等"),table(["场景","设计","验证"],[["外部超时","规范错误码5001/5002；重试上限；降级标注","故障注入TC-030/039"],["JSON解析失败","校验失败循环重试≤批准次数；need_human","TC-028"],["重复上传","file_hash幂等；任务状态机；补偿","TC-009"],["并发修改","version乐观锁；冲突3003","TC-006"],["事务失败","Service统一commit/rollback；外部资源补偿","迁移/上传用例"],["死循环","AGENT_MAX_STEPS=8；超时与取消","TC-028"]]),
        h("安全与日志细节"),bullets("所有业务/AI接口挂require_permission；数据范围在Service/Repository落地。","日志脱敏口令、令牌、手机号、password_hash、答题阶段标准答案。","trace_id贯通HTTP、Service、AI调用、审计；SSE错误事件不泄露堆栈。","文件路径白名单、类型大小限制、预签名有效期；对象存储后端可切换。"),
        h("待详细化的模块"),blanks(["模块 / 类","方法 / 算法","输入输出","异常","Owner","截止"],6),sign(["架构负责人","模块Owner","QA"]),
    ]))
    db=[h("设计依据与现状"),p(f"当前ORM元数据静态导入成功：MySQL {len(mysql)}表、PostgreSQL {len(pg)}表。旧文档中的25/26表口径已过期。字段字典由当前模型元数据生成；生产DDL必须经DBA差异评审，禁止直接照抄历史脚本。"),
        h("四库职责"),table(["存储","职责","关键约束"],[["MySQL","用户/组织/岗位/人才/简历/测评/培训/匹配/知识/会话/文件/审计","OLTP事务、软删除、乐观锁、权限范围"],["PostgreSQL","dim_organization/dim_talent_wide + 4张fact事实表","只读分析、分区、ETL对账、NL2SQL白名单"],["Milvus","人才/知识/题库/测评等向量集合","检索前权限过滤；业务删除同步向量；维度与模型版本一致"],["Redis","chat:mem:*、wf:state:*、限流/配额/锁","TTL、可降级；多实例共享；不存明文敏感信息"]]),
        h("ER关系总览"),table(["关系","说明"],[["users - user_roles - roles","用户角色多对多；权限码与数据范围由角色映射"],["organizations自关联","path/level物化路径；移动防环"],["positions - position_requirements","岗位能力模型多版本；匹配引用确认版本"],["talents - resumes / talent_portraits","人才关联简历与版本画像；软删除与向量一致性"],["assessments - questions/answers/results","测评任务、题目、作答、结果；答案防泄露"],["training_plans - plan_courses - courses / learning_records","计划课程与本人学习记录"],["matching_batches - matching_records - reviews/events","批次快照、匹配记录、HR复核与审计时间线"],["knowledge_docs - chunks/revisions","文档、切片、版本；发布与索引状态"],["conversations - chat_messages","会话与消息；记忆与权限隔离"],["MySQL -> PgSQL dim/fact","ETL增量同步；分析只读；对账与延迟监控"]]),
        h("MySQL字段字典")]
    for t in mysql:
        db += [h2(t['name']),table(["字段","类型","空","键/索引","默认","说明"],[[c['name'],c['type'],"Y" if c['nullable'] else "N",("PK " if c['primary_key'] else "")+("UQ " if c['unique'] else "")+("IDX " if c['index'] else "")+(",".join(c['foreign_keys']) if c['foreign_keys'] else ""),c['server_default'] or c['default'],c['comment']] for c in t['columns']])]
        if t['indexes']: db.append(table(["索引","列","唯一"],[[i['name'],",".join(i['columns']),"Y" if i['unique'] else "N"] for i in t['indexes']]))
    db.append(h("PostgreSQL字段字典"))
    for t in pg:
        db += [h2(t['name']),table(["字段","类型","空","键","默认","说明"],[[c['name'],c['type'],"Y" if c['nullable'] else "N",("PK" if c['primary_key'] else "")+(",".join(c['foreign_keys']) if c['foreign_keys'] else ""),c['server_default'] or c['default'],c['comment']] for c in t['columns']])]
    db += [h("Milvus与Redis设计"),table(["集合 / 键","用途","控制点"],[["当前settings集合名","talent_chunks / knowledge_chunks / question_bank_vectors / assessment_chunks（以配置为准）","维度、metric、权限标量、模型版本、删除同步"],["旧文档集合名","talent_vectors / position_vectors / knowledge_vectors / question_vectors","差异需架构确认，禁止混用"],["chat:mem:{conversation_id}","短期记忆，默认TTL 1h","用户隔离、过期清理"],["wf:state:*","工作流检查点，默认TTL 86400","断点续跑、幂等、取消"],["限流/配额/锁","固定窗口、Token配额、分布式锁","多实例共享、故障降级、释放脚本"]]),
        h("SQL脚本与迁移"),table(["脚本","用途","执行控制"],[[s,"仓库既有脚本；内容需DBA评审","仅在批准环境执行；备份+差异检查+回滚"] for s in f['sql_scripts']]),
        steps("冻结ORM与目标schema差异；生成迁移脚本并评审。","备份数据库与关键文件/向量一致性点。","预生产执行迁移+回滚演练+对账。","生产窗口由DBA执行；应用版本与迁移成对发布。","验证schema、索引、权限、抽样数据与业务冒烟。","归档执行日志、哈希、回滚点与签收。"),
        note("随包附《当前ORM参考DDL.sql》由元数据编译，仅供评审，不是已批准生产迁移。create_all只建缺失表，不会给已有表补列。"),sign(["DBA","架构负责人","数据Owner"]),
    ]
    out.append(S("AITP-03-03","数据库设计说明书（DBD）","03_设计",db,landscape=True))
    api=[h("全局契约"),table(["项","约定"],[["Base URL","同源网关 https://{host}；开发由Vite代理到后端；浏览器禁止直连localhost"],["前缀","/api/v1；探针/health、/health/ready"],["格式","JSON UTF-8；上传multipart/form-data；流式text/event-stream；面试间WebSocket"],["响应","{code,message,data,trace_id}；分页data={items,total,page,page_size,pages}"],["认证","Authorization: Bearer <access_token>；刷新/登出按/auth契约"],["分页","page从1开始，page_size默认20上限以Schema为准；排序字段白名单"],["追踪","X-Trace-Id请求/响应；X-Process-Time-Ms"],["限流","默认300次/60秒/IP；超限429；多实例依赖Redis"],["幂等","GET/PUT/DELETE按契约；创建POST非幂等；上传hash去重；写操作建议业务幂等键"]]),
        h("认证与示例"),code('POST /api/v1/auth/login\n{"username":"<test_user>","password":"<test_password>","remember_me":false}\n\n200 {"code":200,"message":"success","data":{"access_token":"<jwt>","refresh_token":"<jwt>","token_type":"bearer","expires_in":86400},"trace_id":"<trace>"}\n\nGET /api/v1/auth/me\nAuthorization: Bearer <access_token>'),
        note("示例仅为契约形状，凭据必须使用测试账号；不要把真实令牌、口令或个人信息写入文档。"),
        h("错误码"),table(["code","HTTP","含义","前端处理"],[["1001","422","业务参数错误","提示message"],["1002","400","业务规则错误","提示并引导修正"],["2001/2002","401","未登录/令牌过期","登录或刷新"],["2003","401","凭证错误","统一提示，不枚举账号"],["2004","403","权限不足","提示无权限"],["2005/2006","401","禁用/锁定","联系管理员/等待解锁"],["3001","404","资源不存在","返回列表"],["3002/3003","409","重复/乐观锁冲突","刷新重试"],["422","422","请求校验失败","高亮字段"],["429","429","限流","退避重试"],["5000","500","系统错误","稍后重试+trace"],["5001/5002","502","外部服务/AI异常","降级提示+重试"],["5003/5004","400","文件/解析错误","重新上传"],["5005/5006/5007","502","Embedding/向量/MinIO异常","运维定位"]]),
        h("SSE与WebSocket"),code("event: message\ndata: {\"delta\":\"...\"}\n\nevent: citation\ndata: {\"doc_id\":1,\"doc_title\":\"...\",\"page\":2}\n\nevent: done\ndata: {\"conversation_id\":\"...\",\"elapsed_ms\":123}\n\nevent: error\ndata: {\"code\":5001,\"message\":\"...\"}"),
        p("面试间WebSocket路径见端点清单；邀约令牌免登录但必须校验有效期、权限与安全上下文（HTTPS/localhost）。"),
        h("静态端点清单"),note(f"由源码装饰器静态提取{len(routes)}条线索，含探针与WebSocket；应用装配在文档环境导入失败，故未生成运行时OpenAPI。清单不证明接口已联调通过；权限列仅为源码线索，空值需查router依赖。"),
        table(["方法","路径","摘要","权限线索","源码"],[[r['method'],r['path'],r['summary'],",".join(r['permissions']) or "见源码",f"{r['source']}:{r['line']}"] for r in routes]),
        h("请求 / 响应Schema字典"),note("由当前Pydantic model_fields静态导入生成；选择请求/响应/创建/更新/查询/条目等公开契约类。完整212类元数据见随包JSON快照。")]
    selected=[]
    for mod,cs in f['schemas'].items():
        chosen=[(n,c) for n,c in cs.items() if n.endswith(('Request','Response','Create','Update','Query','Item','Parsed','Confirm')) or mod=='auth']
        if not chosen: continue
        api.append(h2(mod)); selected.extend((mod,n) for n,_ in chosen)
        for n,c in chosen:
            api.append(h3(n+(" — "+c['description'] if c['description'] else "")))
            api.append(table(["字段","类型","必填","默认","说明"],[[x['name'],x['type'],"Y" if x['required'] else "N",x['default'],x['description']] for x in c['fields']] or [["（无字段）","","","",""]]))
    api += [h("限流、鉴权与调用示例清单"),bullets("每个写接口必须验证401/403/422/409；每个读接口验证数据范围与脱敏。","上传接口验证类型/大小/路径穿越/预签名过期；分析接口验证SQL注入与越权表。","SSE验证事件顺序、断线、错误事件；Dify降级验证source/channel标注。","调用示例应在联调环境录制并脱敏后归档，绑定提交SHA与trace_id。"),
        h("契约变更与验收"),bullets("接口变更必须更新API Doc、Schema、前端调用、测试用例与RTM，并经架构评审。","验收以真实HTTP联调+自动化测试+UAT为准；静态清单仅作索引。"),sign(["架构负责人","前端负责人","QA","安全"]),
    ]
    out.append(S("AITP-03-04","接口设计文档（API Doc）","03_设计",api,landscape=True))
    out.append(S("AITP-03-05","UI / 原型文档","03_设计",[
        h("设计依据"),p("当前前端为React 19 + TypeScript + Vite + Ant Design 5 + ECharts，品牌名“智才云 TalentOS”。本文把现有页面与交互整理为可评审原型说明；高保真视觉稿与截图由前端在批准环境补充。"),
        h("信息架构与页面"),table(["页面","路由","权限","核心交互"],[[x[0],x[1],x[2],x[3]] for x in PAGES]),
        h("导航与布局"),bullets("左侧分组：工作空间、人才管理、人才发展、智能应用、系统管理；按权限过滤菜单。","顶栏：全局搜索(Ctrl K)、AI助手入口、消息通知、用户菜单/个人中心/修改密码/退出。","内容区：页头标题/描述/操作，卡片化表格与图表；空态显示真实空态，不伪造统计。","人才档案支持全屏/简历CoverFlow/地图等增强交互；面试间需要摄像头麦克风安全上下文。"),
        h("关键页面线框"),code("登录页：左侧品牌视觉 | 右侧账号/密码/记住我/登录\n列表页：页头[标题 描述 操作] → 筛选栏 → 表格 → 分页 → 详情抽屉/弹窗\n匹配页：岗位选择/画像确认 → 执行匹配 → 批次结果(分数/淘汰原因) → 解释证据 → 复核\n知识库：文档/版本列表 → 切片预览 → 检索/问答 → 引用来源面板\n数据洞察：问数输入 → 图表/表格 → 解读 → 导出报告"),
        h("视觉规范"),table(["元素","当前基线","规范"],[["主色","#315efb","按钮/选中/链接；渐变AI入口#315efb→#6956dc"],["背景","#f5f7fb / #fff","页面浅灰、卡片白色；边框#e9edf3"],["文字","#1d2433 / #667085 / #98a2b3","标题/正文/次要；成功#159a70，危险#df5252"],["圆角","卡片12px、按钮/输入8px","保持一致；阴影轻量"],["字体","系统无衬线","中文优先微软雅黑/苹方/思源黑板；表格12px级"]]),
        h("交互与状态"),table(["状态","要求"],[["加载","页面/表格/按钮loading；防重复提交"],["空态","说明原因与下一步；不得用mock填充"],["错误","展示message与trace_id；401跳登录，403提示无权限，429退避"],["权限","菜单/按钮隐藏不替代后端鉴权"],["流式","SSE逐字输出、引用面板、停止/重试、错误事件"],["移动","批准尺寸下核心任务可完成；表格横向滚动或卡片化"]]),
        h("原型验收与交付"),bullets("交付：页面清单、线框/高保真链接、交互说明、视觉规范、浏览器矩阵、截图证据。","验收：核心闭环可操作；权限/空态/错误/流式/移动尺寸通过；无演示数据冒充真实数据。"),kv([["原型工具 / 链接",TBD],["视觉稿版本 / 截图目录",TBD],["批准浏览器 / 移动尺寸",TBD]]),sign(["前端负责人","业务代表","UI / UX","QA"]),
    ]))
    out.append(S("AITP-03-06","部署方案文档","03_设计",[
        h("环境拓扑"),code("用户浏览器 -> TLS网关/反向代理 -> React静态资源 + /api代理 -> FastAPI多实例\n  -> MySQL集群 / PgSQL分析 / Milvus / Redis / 对象存储\n  -> LLM/Embedding/Rerank / Dify / Vanna / OCR / 邮件短信\n  -> 监控告警 / 日志审计 / 备份恢复 / CI制品库"),
        h("环境矩阵"),table(["环境","域名 / 地址","资源规格","数据","Owner"],[["开发",TBD,"本地compose；MySQL/PgSQL，可选Redis/MinIO","脱敏/样本",TBD],["测试",TBD,"独立测试库；外部依赖可切换","脱敏测试集",TBD],["预生产",TBD,"与生产同构或批准差异","迁移演练数据",TBD],["生产",TBD,"应用/DB/向量/GPU/存储按容量模型","真实授权数据",TBD],["国产化",TBD,"批准国产清单","适配样本",TBD]]),
        h("服务器与端口清单"),table(["组件","默认端口 / 线索","规格","数量","备注"],[["FastAPI","8000（settings.PORT）","CPU/内存按压测",TBD,"生产多实例+网关"],["前端","5173开发；生产静态资源","构建产物",TBD,"同源代理，禁止浏览器直连后端localhost"],["MySQL","3306","按OLTP容量",TBD,"主从/备份/只读账号"],["PostgreSQL","5432","按分析容量",TBD,"只读分析+statement_timeout"],["Redis","6379","按记忆/限流容量",TBD,"TTL/持久化/多实例共享"],["Milvus","19530","按向量规模/GPU",TBD,"集合/维度/权限标量"],["MinIO/对象存储","9000/9001或云 endpoint","按文件容量",TBD,"预签名/桶权限"],["Dify/模型","批准 endpoint","GPU/API配额",TBD,"数据出域审批"]]),
        h("部署步骤"),steps("核查制品哈希、依赖锁定、配置模板、密钥注入、域名证书与网络策略。","DBA执行批准迁移；初始化角色/管理员/必要种子数据；禁止默认口令。","部署后端多实例；配置同源代理、CORS明确来源、关闭DEBUG、TLS与安全头。","部署前端构建产物；验证VITE_API_BASE_URL为同源相对路径。","接通四库/对象存储/模型/Dify；逐项健康检查与真实联调。","执行冒烟、权限、上传、RAG、匹配、问数、SSE、邀约安全上下文验证。","开启监控告警、备份、日志审计；值守与回滚准备；发布批准。"),
        h("开发compose警示"),bullets("仓库docker-compose.yml仅供开发联调，含默认口令与本地端口映射；不得直接用于生产。","生产需独立集群、密钥管理、网络隔离、最小端口暴露、备份与HA。","DB_AUTO_CREATE_TABLES生产建议关闭；schema变更走DBA迁移。"),
        h("资源规格与容量"),table(["维度","输入","估算 / 决策"],[["在线用户","2000目标","并发请求率、会话时长、SSE连接数压测确定实例数"],["数据量","人才/简历/制度/测评/培训",TBD,"MySQL/PgSQL容量与分区策略"],["向量","条数×维度×副本",TBD,"Milvus内存/磁盘与检索延迟"],["模型","Token/调用量/GPU",TBD,"配额、分级调用、私有化许可"],["文件","数量×平均大小×保留期",TBD,"对象存储生命周期与备份"]]),
        h("验证与回滚"),bullets("验证：/health、/health/ready、核心API、前端登录、上传、检索、匹配、问数、SSE、审计。","回滚：保留上一制品/配置/迁移回滚点；按《回滚方案》执行并数据对账。"),sign(["运维","DBA","架构","安全","项目经理"]),
    ]))
    out.append(S("AITP-03-07","安全设计方案","03_设计",[
        h("安全目标与边界"),p("保护简历、手机号、测评、培训轨迹、制度知识与账号权限；满足原需求提出的网络安全、数据安全、个人信息保护与私有化目标。合规结论与等保测评由有权主体确认，本方案不替代证书。"),
        h("威胁模型"),table(["威胁","场景","控制"],[["越权/侧信道","RAG先召回后过滤、NL2SQL跨部门、Skill伪造身份","检索前过滤、AST白名单、服务端AIContext、行/字段级隔离"],["注入","SQL/命令/Prompt注入、路径穿越","AST校验、参数化ORM、文件白名单、输入校验、最小权限"],["泄露","日志/导出/预签名/模型出域","脱敏、导出审计、短有效期、私域部署与合同条款"],["滥用","刷课、作弊、恶意上传、限流绕过","进度防刷、监考事件、类型大小限制、Redis共享限流"],["供应链","依赖漏洞、默认口令、密钥入库","依赖扫描、密钥托管、生产基线、制品哈希"],["AI错误决策","幻觉、无证据匹配/评分","引用证据、无依据拒答、人工复核、评测与申诉"]]),
        h("权限模型"),table(["角色","数据范围","权限要点"],[[k,v,",".join(f['permissions']['role_permissions'].get(k,[]))] for k,v in f['permissions']['data_scope'].items()]),
        p("当前权限码："+", ".join(f['permissions']['permissions'])),
        h("数据安全"),table(["数据","分级","控制"],[["口令/令牌/密钥","极敏感","哈希/密钥管理；日志与文档禁止明文；轮换"],["简历/手机号/测评","敏感","最小必要、脱敏、权限、导出审计、retention"],["制度/岗位/培训","内部","分级权限、版本与发布审核"],["统计/报表","内部/敏感","范围注入、下载鉴权、快照有效期"],["审计日志","敏感","防篡改、访问控制、保留期与脱敏"]]),
        h("应用与AI安全"),bullets("全站TLS；CORS明确来源；安全头；DEBUG生产关闭；限流默认300/60s/IP并按环境调整。","JWT双令牌、过期/吊销策略、登录锁定；密码bcrypt/argon2按当前实现核验。","RAG权限前置expr+组织过滤；无依据拒答；引用可溯源；模型/Embedding/OCR出域审批。","NL2SQL仅单条SELECT/WITH、白名单表、禁止DDL/DML/系统表、范围注入、LIMIT、只读账号与超时。","文件上传防路径穿越、类型大小校验、预签名过期、下载鉴权；对象存储桶最小权限。","Agent/Skill忽略客户端身份参数；最大步数、超时、重试上限、need_human与审计。"),
        h("审计、备份与应急"),bullets("记录登录、授权变更、导出、AI调用、数据变更、发布与运维操作；trace_id贯通。","备份MySQL/PgSQL/文件/向量/AI资产；恢复演练；安全事件按预案隔离、轮换、取证、通报。"),
        h("合规与等保路径"),steps("确认个人信息处理合法性基础、告知同意、最小必要与保留期。","数据出域/供应商/模型部署位置安全与合同审查。","等保定级备案、差距评估、整改、测评（如适用）。","安全测试与高危清零；国产化适配安全核查。","形成安全评审结论与残余风险批准。"),
        h("安全验收清单"),table(["项","标准","证据"],[["越权用例","全部拒绝且留痕","TC-003/004/024/029"],["敏感日志","无明文口令/令牌/完整手机号","TC-032+日志抽检"],["文件安全","穿越/类型/大小/预签名受控","TC-031"],["AI边界","无依据拒答、降级标注、私域审批","TC-013/030"],["高危漏洞","清零或有权主体批准残余","安全测试报告"]]),sign(["安全负责人","架构负责人","合规 / 法务","项目经理"]),
    ]))
    # 04 执行与测试
    out.append(S("AITP-04-01","开发 / 实施周报","04_执行与测试",[
        h("本周概览"),kv([["报告周期",FILL+" 至 "+FILL],["项目经理 / 版本",TBD],["整体状态","□绿 □黄 □红；原因："+FILL],["本周里程碑",FILL],["预算 / 工时",FILL]]),
        h("进度与完成内容"),table(["WBS / 需求","计划","实际","完成%","证据 / 提交","偏差原因"],[[w[0]+" "+w[1],FILL,FILL,FILL,FILL,FILL] for w in WBS[:8]]),
        h("问题、风险与变更"),table(["类型 / ID","描述","级别","Owner","截止","状态 / 升级"],[["问题",FILL,FILL,TBD,TBD,FILL],["风险",FILL,FILL,TBD,TBD,FILL],["变更",FILL,FILL,TBD,TBD,FILL]]),
        h("下周计划"),table(["任务 / WBS","目标交付","Owner","依赖","截止"],[[FILL,FILL,TBD,FILL,TBD] for _ in range(5)]),
        h("需决策事项"),blanks(["事项","背景 / 影响","建议","决策人","截止"],3),
        h("质量与指标"),table(["指标","本周","累计","阈值","动作"],[["用例通过率",FILL,FILL,FILL,FILL],["P0/P1缺陷",FILL,FILL,"0",FILL],["AI降级比例",FILL,FILL,FILL,FILL],["成本/进度偏差",FILL,FILL,FILL,FILL]]),note("周报不得把历史测试数字或静态代码存在写成本周实测；降级能力必须如实标注。"),sign(["项目经理","技术负责人"]),
    ],status="待执行记录模板"))
    out.append(S("AITP-04-02","开发 / 实施月报","04_执行与测试",[
        h("月度摘要"),kv([["月份 / 编制人",FILL],["总体状态","□绿 □黄 □红"],["里程碑达成",FILL],["预算执行 / 预测",FILL],["重大风险 / 决策",FILL]]),
        h("目标达成与交付"),table(["目标 / WBS","计划交付","实际交付","验收证据","偏差 / 纠偏"],[[FILL,FILL,FILL,FILL,FILL] for _ in range(6)]),
        h("质量、性能与AI效果"),table(["指标","目标","本月实测","趋势","结论 / 动作"],[["用例通过率 / P0P1","批准准出 / 0",FILL,FILL,FILL],["解析/检索/匹配准确率","99%/95%/90%目标",FILL,FILL,FILL],["性能","2000在线/1s/500ms/5s",FILL,FILL,FILL],["外部依赖降级","如实标注",FILL,FILL,FILL]]),
        h("风险问题变更TOP"),table(["ID","类型","描述","影响","应对 / 决策","Owner"],[[FILL,FILL,FILL,FILL,FILL,TBD] for _ in range(5)]),
        h("成本与资源"),table(["账户","预算","实际","预测EAC","偏差","动作"],[[x[0],TBD,FILL,FILL,FILL,FILL] for x in BUDGET[:6]]),
        h("下月计划与需管理层决策"),blanks(["计划 / 决策","目标","Owner","资源","截止"],5),sign(["项目经理","发起人 / 甲方代表"]),
    ],status="待执行记录模板"))
    out.append(S("AITP-04-03","会议纪要","04_执行与测试",[
        h("会议信息"),kv([["会议主题",FILL],["类型","□评审 □站会 □问题协调 □方案讨论 □CCB □验收"],["时间 / 地点 / 主持",FILL],["记录人 / 纪要版本",FILL],["参会 / 缺席",FILL]]),
        h("议程与讨论要点"),table(["议题","汇报 / 讨论","结论倾向","材料"],[[FILL,FILL,FILL,FILL] for _ in range(4)]),
        h("决议"),table(["编号","决议内容","依据 / 影响","批准人","生效条件"],[[f"D-{i:02d}",FILL,FILL,TBD,FILL] for i in range(1,4)]),
        h("行动项"),table(["编号","行动","责任人","截止","验收证据","状态"],[[f"A-{i:02d}",FILL,TBD,TBD,FILL,"待办"] for i in range(1,6)]),
        h("遗留与升级"),blanks(["问题 / 风险","影响","升级对象","截止"],3),
        h("确认与分发"),bullets("纪要24h内分发；参会人确认决议与行动项；异议在批准时限内书面提出。","涉及范围/预算/契约/安全的决议必须关联变更单或审批，不替代正式签字。"),sign(["主持人","记录人","关键决议确认人"]),
    ],status="待执行记录模板"))
    issues=[
        ["ISS-001","文档与代码状态漂移","P1","打开","旧SRS/README/交付报告状态、表数量与当前基线不一致","以当前元数据+RTM+实测更新；文档评审冻结","PM/架构",TBD,""],
        ["ISS-002","文档环境应用装配导入失败","P0核验","打开","create_application导入app.ai.proctoring.evaluate_proctor失败；完整依赖环境未复核","锁定环境复现；修复/澄清；补启动冒烟；未清不得上线","架构",TBD,""],
        ["ISS-003","外部AI/向量真实联调证据不足","P1","打开","历史报告提示Dify/LLM/Milvus降级，不能冒充目标能力","真实联调+效果评测；响应标注来源","Dify/RAG",TBD,""],
        ["ISS-004","当前基线性能/安全实测缺失","P1","打开","缺少2000在线、500ms、5s与安全高危清零证据","批准压测/安全/国产化测试并归档","QA/运维/安全",TBD,""],
        ["ISS-005","测试库并发污染风险","P2","打开","历史报告记录并发pytest共用drop_all导致假失败","独立测试库/统一夹具/串行建表；慢用例隔离","QA",TBD,""],
    ]
    out.append(S("AITP-04-04","问题台账（Issue Log）","04_执行与测试",[
        h("台账说明"),p("记录已发生问题；风险是未发生不确定性。P0阻断上线/数据安全，P1核心能力，P2非核心，P3优化。关闭必须有解决方案、验证证据与关闭时间。"),
        h("问题清单"),table(["ID","问题","优先级","状态","描述 / 影响","解决方案","Owner","截止","关闭时间 / 证据"],issues),
        h("新增问题"),blanks(["ID","问题","优先级","状态","描述 / 影响","解决方案","Owner","截止","关闭时间 / 证据"],5),
        h("升级与关闭规则"),bullets("P0立即升级PM/技术/安全/发起人；P1 24h内响应；每日站会跟踪打开项。","关闭需Owner提交证据、QA/提出人验证、PM确认；重开保留历史。"),sign(["项目经理","QA"]),
    ],status="预填台账 + 待执行记录",landscape=True))
    out.append(S("AITP-04-05","测试计划","04_执行与测试",[
        h("测试范围与策略"),table(["层级","范围","方法 / 工具","准入"],[["L1 单元","规则/函数/Schema/安全校验","pytest；离线Mock；不连生产","代码自检+构建"],["L2 集成","Service→Repository→真实测试库","pytest+MySQL测试库；夹具隔离","独立测试库就绪"],["L3 接口","HTTP契约/权限/错误码/SSE","httpx/ASGI或真实进程；OpenAPI核对","冒烟通过"],["L4 端到端","简历/匹配/RAG/测评/培训/问数","脚本+浏览器手测；真实外部服务","依赖接通或批准降级边界"],["L5 非功能","性能/安全/国产化/恢复/故障注入","压测工具/安全检查/演练","环境与容量模型批准"],["L6 UAT","业务场景验收","甲方代表执行UAT方案","测试准出+数据脱敏"]]),
        h("测试环境"),kv([["代码提交 / 制品",TBD],["Python / 依赖锁定",TBD],["MySQL/PgSQL/Milvus/Redis/对象存储",TBD],["LLM/Dify/Vanna/OCR endpoint与版本",TBD],["测试数据集 / 脱敏批准",TBD],["浏览器 / 移动尺寸 / 国产环境",TBD]]),
        h("准入 / 准出"),bullets("准入：需求基线冻结、环境就绪、数据脱敏、制品可追溯、冒烟通过。","准出：批准用例执行完成；P0/P1=0；P2批准遗留；性能/安全/国产化达标；证据绑定版本环境。","外部模型慢用例与离线用例分层；禁止并发共用会drop_all的测试库。","历史183/268等数字仅作线索，不继承为本次结论。"),
        h("进度与资源"),table(["阶段","窗口","Owner","交付"],[["计划/用例评审",TBD,"QA","测试计划/用例"],["单元/集成",TBD,"各组","执行记录"],["接口/E2E",TBD,"QA/各组","报告/缺陷"],["非功能",TBD,"QA/运维/安全","压测/安全/国产化"],["回归/UAT准备",TBD,"QA/PM","准出报告"]]),
        h("缺陷与风险"),table(["级别","定义","SLA"],[["P0","数据丢失/泄露/不可用/核心阻断","立即停止发布，修复回归复盘"],["P1","核心错误/越权/指标不达标","上线前清零"],["P2","非核心可绕过","批准遗留与计划"],["P3","体验优化","纳入迭代"]]),bullets("风险：外部依赖未接通、测试污染、性能不达标、AI效果口径争议、数据迁移不一致；应对见风险登记册。"),sign(["QA负责人","项目经理","技术负责人","业务代表"]),
    ]))
    out.append(S("AITP-04-06","测试用例","04_执行与测试",[
        h("用例说明"),p("用例覆盖RTM中的P0/P1需求；执行时填写实际结果、证据、缺陷ID与执行人。预期结果为批准口径，目标指标需业务/QA确认评测集。"),
        h("用例清单"),table(["ID","需求","模块","场景","前置 / 数据","预期结果","实际 / 证据","状态"],[[t[0],t[1],t[2],t[3],t[4],t[5],FILL,"待执行"] for t in TESTS]),
        h("新增用例"),blanks(["ID","需求","模块","场景","前置 / 数据","预期结果","实际 / 证据","状态"],5),
        h("执行规则"),bullets("每条用例记录提交SHA、环境、数据集、执行人、时间、trace_id/日志/截图。","失败必须建缺陷并回溯需求；通过不得仅凭静态代码或历史报告。"),sign(["QA","用例评审人"]),
    ],status="预填用例 · 待执行",landscape=True))
    checks=f['checks']
    out.append(S("AITP-04-07","测试报告（一轮 / 回归）","04_执行与测试",[
        h("报告信息与结论"),kv([["报告类型","□一轮 □回归 □准出"],["代码提交 / 制品 / 环境",TBD],["执行窗口 / 执行人",TBD],["总体结论","□通过 □附条件通过 □不通过 □未完成"],["准出建议",FILL]]),
        h("本次已完成的文档编制静态检查"),table(["检查","结果","边界"],[["Python版本",checks['python'],"文档编制环境，非生产运行环境"],["源码语法AST检查",f"{checks['syntax_files']}个文件，{len(checks['syntax_errors'])}个语法错误","不证明业务正确"],["ORM元数据导入",f"MySQL {len(mysql)}表 / PgSQL {len(pg)}表","不证明生产schema一致"],["Pydantic Schema导入","23个模块导入成功","不证明HTTP联调"],["应用装配导入",checks['application_import']['status']+"："+checks['application_import']['detail'],"最小依赖环境；完整环境需复核，列为上线前核验项"]]),
        note("以上仅为文档编制过程的静态检查，不是功能测试/压测/安全测试结论。未运行应用业务测试、真实外部服务联调或UAT。"),
        h("功能测试执行汇总"),table(["层级 / 模块","用例数","通过","失败","阻塞","未执行","证据"],[[x,FILL,FILL,FILL,FILL,FILL,FILL] for x in ["L1 单元","L2 集成","L3 接口","L4 E2E","L5 非功能","回归"]]),
        h("缺陷与遗留"),table(["缺陷ID","级别","需求","描述","状态","修复版本","回归证据"],[[FILL,FILL,FILL,FILL,FILL,FILL,FILL] for _ in range(5)]),
        h("历史报告参考"),bullets("仓库docs/TEST_STATUS.md、docs/delivery/05~09等记录过历史环境与数字；存在过期与口径差异。","引用历史报告必须写明原日期、提交、环境、外部依赖状态；不得复制到本次结论。"),
        h("准出检查"),table(["项","标准","结果 / 证据"],[["批准用例执行","完成且可追溯",FILL],["P0/P1","0",FILL],["性能/安全/国产化","批准报告",FILL],["外部依赖","真实联调或批准降级边界",FILL],["UAT准备","场景/数据/人员就绪",FILL]]),sign(["QA负责人","技术负责人","项目经理","业务代表"]),
    ],status="待执行报告模板"))
    out.append(S("AITP-04-08","Bug 清单（缺陷报告）","04_执行与测试",[
        h("缺陷字段规范"),table(["字段","要求"],[["ID / 标题","唯一编号+可检索标题"],["需求 / 版本 / 环境","REQ ID、提交SHA、环境、数据集"],["复现步骤 / 预期 / 实际","可复现；附trace_id/日志/截图脱敏"],["级别 / 优先级","P0~P3；影响与紧急度分开"],["Owner / 修复 / 回归","责任人、修复版本、回归用例与证据"],["状态","新建/确认/修复中/待回归/关闭/重开/拒绝（需理由）"]]),
        h("缺陷清单"),table(["ID","标题","需求 / 版本","级别","状态","复现 / 影响","Owner","修复版本","回归证据 / 关闭"],[
            ["BUG-001","文档环境装配导入失败","REQ-001 / "+f['baseline'][:8],"P0核验","新建",checks['application_import']['detail'],"架构",TBD,TBD],
            ["BUG-002","文档与代码状态漂移","REQ-001~033","P1","新建","旧文档状态/表数量与当前基线不一致","PM/架构",TBD,TBD],
        ]+[["【待填写】"]*9 for _ in range(5)]),
        h("关闭与重开"),bullets("关闭需QA回归通过并提出人确认；重开保留历史与原因。","P0/P1未清不得上线；拒绝缺陷需书面理由与仲裁。"),sign(["QA","开发负责人"]),
    ],status="预填缺陷 + 待执行记录",landscape=True))
    out.append(S("AITP-04-09","性能测试报告","04_执行与测试",[
        h("测试目标与口径"),table(["指标","原需求目标","批准口径 / 百分位"],[["在线用户","≥2000","并发模型、思考时间、会话时长待批准"],["普通接口","平均≤1s","建议P95/P99同时报告"],["向量检索","≤500ms","数据规模、top_k、过滤条件"],["大数据报表","≤5s","数据量、查询复杂度、缓存"],["页面渲染","≤1s","批准浏览器与网络"],["SSE TTFT","≤2s（SRS建议）","首事件口径"],["可用率","系统99.9% / AI99.5%","统计窗口与排除项"],["缓存命中率","≥98%（原需求）","热点定义与采样"]]),
        h("环境与 workload"),kv([["提交 / 制品 / 环境",TBD],["硬件 / 实例数 / 四库规格",TBD],["数据规模 / 向量维度 / 模型版本",TBD],["压测工具 / 脚本 / 持续时间",TBD],["混合比例","登录/CRUD/上传/检索/匹配/问数/SSE：待批准"]]),
        h("结果记录"),table(["场景","并发","TPS/QPS","Avg","P95","P99","错误率","资源瓶颈","结论"],[[x,FILL,FILL,FILL,FILL,FILL,FILL,FILL,FILL] for x in ["普通CRUD","向量检索","RAG问答","人岗匹配","NL2SQL报表","SSE对话","混合2000在线"]]),
        h("瓶颈与优化"),blanks(["现象","根因","优化动作","复测结果","Owner"],4),
        h("结论"),kv([["是否达到批准目标","□是 □否 □部分；说明："+FILL],["遗留 / 风险",FILL],["准出建议",FILL]]),note("未提供实测数据；不得用目标值填充结果。历史报告未做并发压测的说明需复核。"),sign(["QA / 性能","运维","架构","项目经理"]),
    ],status="待执行报告模板",landscape=True))
    out.append(S("AITP-04-10","安全测试报告","04_执行与测试",[
        h("范围与依据"),p("覆盖认证授权、数据范围、RAG/NL2SQL/Skill安全、文件上传、日志脱敏、配置基线、依赖漏洞、私域边界与审计。依据安全设计方案、批准威胁模型与适用合规要求；等保测评由有权机构执行。"),
        h("环境与授权"),kv([["提交 / 环境 / 窗口",TBD],["测试授权人 / 范围边界",TBD],["工具 / 版本 / 账号角色",TBD],["禁止事项","不得对未授权生产系统测试；不得导出真实个人信息到公开环境"]]),
        h("测试项与结果"),table(["类别","测试项","预期","结果 / 证据","级别","状态"],[
            ["认证","暴力破解/锁定/令牌过期/吊销","策略生效",FILL,FILL,"待执行"],
            ["授权","水平/垂直越权、跨部门、字段脱敏","403/脱敏/审计",FILL,FILL,"待执行"],
            ["RAG","权限前置过滤、无依据拒答、引用溯源","不串库/不编造",FILL,FILL,"待执行"],
            ["NL2SQL","DDL/DML/系统表/跨范围/超时","全部拒绝或受控",FILL,FILL,"待执行"],
            ["Skill/Agent","伪造身份、Prompt注入、死循环","服务端上下文/步数限制",FILL,FILL,"待执行"],
            ["文件","路径穿越/类型/大小/预签名","拒绝非法/过期失效",FILL,FILL,"待执行"],
            ["日志","口令/令牌/手机号泄露","脱敏",FILL,FILL,"待执行"],
            ["配置","DEBUG/CORS/默认口令/TLS/安全头","生产基线通过",FILL,FILL,"待执行"],
            ["依赖","已知漏洞/密钥扫描","高危清零",FILL,FILL,"待执行"],
            ["审计","敏感操作留痕/防篡改","可追溯",FILL,FILL,"待执行"],
        ]),
        h("漏洞台账"),blanks(["ID","漏洞","级别","影响","修复","复测","Owner","状态"],5),
        h("结论与残余风险"),kv([["高危 / 中危 / 低危数量",FILL],["是否允许上线","□是 □否 □附条件"],["残余风险批准人",TBD],["合规 / 等保边界","本报告会否替代正式测评：否；正式结论："+TBD]]),sign(["安全测试","安全负责人","架构","项目经理"]),
    ],status="待执行报告模板",landscape=True))
    out.append(S("AITP-04-11","版本发布说明（Release Note）","04_执行与测试",[
        h("版本信息"),kv([["版本号 / 标签",TBD],["发布日期 / 窗口",TBD],["代码提交 / 制品哈希",TBD],["上一版本 / 回滚点",TBD],["发布类型","□ major □ minor □ patch □ hotfix"],["影响范围","后端/前端/DB/向量/Prompt/工作流/配置：待填写"]]),
        h("新增功能"),table(["需求 / WBS","功能","用户价值","开关 / 权限","证据"],[[FILL,FILL,FILL,FILL,FILL] for _ in range(4)]),
        h("修复与优化"),table(["缺陷ID","问题","修复","回归证据","影响"],[[FILL,FILL,FILL,FILL,FILL] for _ in range(4)]),
        h("已知问题与降级"),table(["ID","问题 / 降级","影响","绕过 / 计划","是否阻断"],[[FILL,FILL,FILL,FILL,FILL] for _ in range(3)]),note("必须如实标注rule_fallback/sql_fallback/Dify或Milvus未接通等边界；不得把降级写成目标AI能力。"),
        h("部署注意事项"),bullets("前置检查：备份、迁移审批、配置差异、密钥、依赖锁定、制品哈希、窗口与值守。","数据库：执行批准迁移；禁止直接照抄旧DDL；create_all不补列。","配置：Prompt/工作流/模型版本、CORS、DEBUG、限流、对象存储与Milvus集合名。","验证：健康探针、登录、核心闭环、权限、上传、RAG、匹配、问数、SSE、审计。"),
        h("回滚方案"),kv([["回滚触发",FILL],["回滚步骤 / 责任人","见《回滚方案》；本版差异："+FILL],["数据兼容性 / 对账",FILL],["演练证据",TBD]]),sign(["发布负责人","运维","DBA","QA","项目经理"]),
    ],status="待执行记录模板"))
    out.append(S("AITP-04-12","上线 / 实施方案","04_执行与测试",[
        h("上线目标与窗口"),kv([["版本 / 制品 / 提交",TBD],["上线窗口 / 停机时长",TBD],["影响范围 / 用户通知",TBD],["总指挥 / 运维 / DBA / 业务验证",TBD],["回滚决策人 / 最晚回滚点",TBD],["批准单 / 变更单",TBD]]),
        h("前置检查"),table(["检查项","标准","责任人","结果"],[["测试准出","P0/P1=0；性能/安全/回归批准","QA",FILL],["UAT/试运行","批准结论与遗留清单","业务/PM",FILL],["备份","DB/文件/向量/AI资产可恢复","运维/DBA",FILL],["迁移","DBA批准脚本+预生产演练","DBA",FILL],["配置","密钥/CORS/DEBUG/限流/集合名/模型版本","运维/安全",FILL],["制品","哈希一致、依赖锁定、无密钥","CI/运维",FILL],["监控告警","探针/日志/指标/值守接通","运维",FILL],["回滚","演练通过、回滚点明确","运维/DBA",FILL],["通知","用户/甲方/支持渠道已知窗口","PM",FILL]]),
        h("实施步骤"),table(["步骤","时间","动作","执行人","验证 / 失败处理"],[
            ["T-7d",TBD,"冻结范围/制品；完成演练与审批","PM/运维","批准单归档"],
            ["T-1d",TBD,"全量备份；配置/迁移复核；通知确认","DBA/运维","备份恢复抽验"],
            ["T0",TBD,"进入停机/灰度窗口；挂维护页","运维","公告生效"],
            ["T1",TBD,"DBA执行迁移；schema差异检查","DBA","失败即评估回滚"],
            ["T2",TBD,"部署后端/前端/配置；启动多实例","运维","/health与/health/ready"],
            ["T3",TBD,"初始化/补偿任务；接通外部依赖","运维/各组","依赖健康或批准降级"],
            ["T4",TBD,"冒烟：登录/权限/上传/RAG/匹配/问数/SSE/审计","QA/业务","核心闭环通过"],
            ["T5",TBD,"业务验证与监控观察；解除维护","业务/运维","指标稳定"],
            ["T+1d",TBD,"复盘值守；问题台账；发布说明归档","PM/运维","上线记录签字"],
        ]),
        h("验证清单"),bullets("健康探针、登录与角色菜单、组织数据范围、简历上传解析、知识检索引用、匹配批次证据、测评培训闭环、NL2SQL与报告导出、SSE、文件预签名、审计trace。","错误率、P95/P99、外部依赖降级比例、模型配额、数据库连接池、向量索引状态、对象存储容量。"),
        h("失败与回滚"),p("任一关键验证失败、数据不一致、安全事件或超过最晚回滚点，由回滚决策人启动《回滚方案》。禁止在未对账情况下继续放量。"),
        h("沟通与值守"),table(["角色","职责","联系方式 / 备份"],[["总指挥","决策与对外沟通",TBD],["运维","部署/监控/回滚",TBD],["DBA","迁移/数据对账",TBD],["QA/业务","验证与签收",TBD],["支持","用户工单与公告",TBD]]),sign(["项目经理","运维","DBA","安全","业务 / 甲方","批准人"]),
    ],status="核心上线模板 · 待批准"))
    out.append(S("AITP-04-13","回滚方案","04_执行与测试",[
        h("回滚目标与原则"),bullets("在批准窗口内恢复到上一稳定版本与一致数据状态；优先保数据与安全。","回滚不是简单重启：必须覆盖应用、配置、数据库、向量、文件、Prompt/工作流与缓存。","未对账不得宣布恢复成功；破坏性迁移需预先批准逆向脚本或恢复点。"),
        h("触发条件与决策"),table(["触发","阈值 / 场景","决策人","时限"],[["核心不可用","登录/核心闭环失败且短时无法修复",TBD,TBD],["数据不一致","迁移/对账失败、丢失/重复风险",TBD,TBD],["安全事件","越权/泄露/高危漏洞",TBD,"立即"],["性能严重退化","错误率/P99超批准阈值",TBD,TBD],["窗口超限","超过最晚回滚点",TBD,"立即"]]),
        h("回滚准备"),table(["对象","回滚点 / 动作","Owner","验证"],[["应用制品","上一版本镜像/dist+哈希","运维","启动冒烟"],["配置","上一配置快照；密钥不回退泄露值","运维/安全","配置差异核查"],["数据库","逆向迁移或备份恢复；双写/兼容策略","DBA","schema+抽样对账"],["向量","上一索引快照/重建；删除孤儿向量","RAG/运维","检索抽样与权限"],["文件","对象存储版本/备份；元数据一致","运维","上传下载抽验"],["AI资产","Prompt/工作流/模型上一版本","Dify/Agent","评测抽检"],["缓存","清理失效会话/状态/限流键","运维","登录与会话验证"]]),
        h("执行步骤"),steps("总指挥宣布回滚，停止放量/挂维护页，通知干系人。","冻结写入或进入批准只读模式；记录当前数据位点与日志。","DBA执行逆向迁移/恢复；运维回退应用与配置。","回退向量/文件/AI资产；清理缓存与失效任务。","启动上一版本；健康探针与依赖检查。","数据对账：数量、关键字段、批次/版本、文件/向量一致性。","业务冒烟与权限验证；监控观察；解除维护。","发布回滚公告与事件记录；24h内复盘并补变更单。"),
        h("数据对账与验收"),table(["检查","方法","通过标准","结果"],[["记录数/关键汇总","源备份/位点对比","差异在批准阈值内",FILL],["匹配批次/画像版本","抽样回读","版本与证据一致",FILL],["文件/向量","抽样下载/检索","元数据与内容一致",FILL],["权限/审计","越权用例+日志","无泄露且留痕",FILL],["性能/错误率","监控窗口","恢复稳定",FILL]]),
        h("演练与批准"),kv([["最近演练日期 / 环境",TBD],["演练耗时 / 是否窗口内",TBD],["演练问题与修复",FILL],["回滚授权人 / 备份",TBD]]),sign(["运维","DBA","架构","安全","总指挥 / 批准人"]),
    ],status="核心回滚模板 · 待演练"))
    return out
