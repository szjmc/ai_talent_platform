# 后端业务数据完整性审计与修复说明

标签：[架构组] [P0]

## 结论

本轮把测评、简历组卷、邀约通知、分析 ETL、匹配/岗位技能、组织数据范围等链路中的
“内置试卷 / 固定分数 / 模拟发送成功 / 旧接口签名降级 / 缺数据补零”改为真实数据库事实、
显式配置和结构化失败。外部 HTTP 接口路径、方法、请求/响应 Schema 未改变。

离线回归已通过；MySQL 集成测试因环境无法连接 MySQL 仍未验收通过，不能把 skip/error 当作通过。

## 关键修复

### 测评与简历组卷

- `app/ai/agents/assessment_dify_client.py`
  - 删除 assign/score/analyze/report 的测试数据兜底。
  - 未配置 `DIFY_WF_*`、`DIFY_BASE_URL`、`DIFY_API_KEY` 时抛 `ExternalServiceError`。
  - 校验 HTTP、JSON、`data.status=succeeded` 和非空 `outputs`。
  - 请求体实际携带配置的 `workflow_id`。
- `app/ai/agents/resume_parser.py`
  - 题目与标准答案只来自已发布且当前身份可读的数据库题库。
  - 语义检索失败时仍走同一授权范围的数据库随机选题，不再回退内置试卷。
  - 旧同步入口 `retrieve_questions()` 明确拒绝，要求使用异步数据库入口。
  - 未识别学历不再默认本科/学校层级 3。
- `app/services/assessment_service.py`
  - 内部 `create_assessment()` 改为授权数据库题库组卷，调用方需传入可信 `user`。
  - 提交答案前锁定测评主记录，防止并发重复提交。
  - 评分分析读取不再长时间持有行锁；保存结果时在保存点内锁定并校验状态。
  - 自动建档不再把 `files.id` 误写成 `resumes.id` 逻辑外键。
  - 面试记录上传校验图片内容、会话归属，文件写入走线程池。
  - 报告只使用真实证据；缺失面试/学历不补零，岗位平均分无样本返回 `None`。
- `app/ai/agents/assessment_agent.py` / `assessment_nodes.py`
  - `stage` 仅允许 `assign/full`；`full` 只评数据库已提交答案，不重新组卷或重复发信。
  - 客观题按数据库标准答案重判，不信任请求中的分数。
  - 主观题模型输出必须字段齐全且 0–100；非法输出结构化失败。
  - `assign` 阶段邮件失败时 `ok=False` 并回传 `email_result`，不伪报下发成功。
- `app/ai/agents/interview_record_parser.py`
  - 面试得分缺失/0 视为未知，超出 0–100 视为非法，不写入伪造分数。

### 通知与邀约

- `app/infrastructure/notify/email_client.py`、`app/services/invite_notify.py`
  - SMTP 未启用/配置不完整/发送失败均返回 `ok=False, simulated=False`。
  - 不再模拟发送成功；SMTP 未启用 TLS/SSL 时拒绝明文发送凭据。
- `app/services/invitation_service.py`
  - 邀约链接必须来自 `INVITE_BASE_URL / FRONTEND_URL / PUBLIC_BASE_URL`。
  - `sent` 状态只在真实渠道发送成功后设置。
- `app/schemas/invitation.py`、`app/routers/invite_router.py`
  - 保留兼容字段，但文档不再承诺 mock 发送；当前发送端不会返回 mock 成功。

### 分析 ETL 与研判工作流

- `scripts/sync_analytics.py`
  - 对齐当前 ORM：移除不存在的 `talent_no`，测评 JOIN 有效结果，培训使用 `plan_id/progress/event_at`。
  - `dim_organization.path` 与 `dim_talent_wide.org_path` 统一为“只含祖先”的口径。
  - 匹配平均分过滤软删除记录；无匹配分保留 `None`，不补 0。
  - 当前月快照在事务内先删后插，避免离职/调岗/学历变更后残留旧分组；历史月份不伪造重建。
  - 事实表窗口替换使用带时区边界；源端合法为空时差集清理会清空目标维表。
  - 成功水位使用实际完成时间，失败不推进水位。
- `app/ai/workflows/analysis_workflow.py`
  - `run_analysis_workflow()` 迁移到当前 `AnalyticsService` 签名，需要可信登录用户。
  - 只聚合真实分布、测评摘要和人才存量趋势；离职趋势、培训完成率、匹配率缺数据源时保留 `None`。
  - 不再用旧签名探测失败后伪造 `total_talents=0`。

### 技能、组织数据范围与向量库

- `app/ai/skills/matching_skill.py`
  - 匹配打分/候选人列表改读 `MatchingService.get_latest()` 的持久化批次结果。
  - 岗位检索改用 `PositionRepository.paginate()` 白名单搜索，并按可信组织范围过滤。
  - 函数式入口走 `BaseSkill.run()`，不绕过权限/参数校验。
- `app/repositories/organization_repository.py`、`app/services/talent_service.py`、`app/services/user_service.py`
  - 修复子树查询：`organizations.path` 只含祖先，必须用 `path + 自身ID + "/"` 查子孙，避免经理看到兄弟部门。
- `app/adapters/vector_store/talent_vector_store.py`
  - `upsert` 使用 Milvus `client.upsert`，确保覆盖写而不是重复插入。
- `scripts/smoke_assessment.py`
  - 冒烟向量测试使用随机临时集合，不再删除业务集合 `interview_record_vectors`。

## 外部契约保持

OpenAPI 基线比对结果：

- baseline paths：124
- current paths：124
- baseline routes：142
- current routes：142
- missing / added / changed_operations / changed_schemas：均为空

未注册 `invite_router`，未恢复 positions/assessments/trainings/matchings 的 501 占位。
`SuccessResponse` 兼容类保留，避免 OpenAPI 组件名漂移。

## 验证结果

在后端目录 `ai_talent_platform/ai_talent_platform` 执行：

```bash
/home/user/.venv/bin/python -m pytest tests --ignore=tests/integration -o addopts='' -q
/home/user/.venv/bin/python -m compileall -q app scripts tests
/home/user/.venv/bin/python /home/user/check_openapi.py
/home/user/.venv/bin/python scripts/smoke_assessment.py
/home/user/.venv/bin/python scripts/demo_assessment.py
```

结果：

- 离线 pytest：`976 passed, 59 skipped, 1 warning`，exit code 0。
- compileall：通过。
- OpenAPI：零差异。
- `scripts/smoke_assessment.py`：通过。
- `scripts/demo_assessment.py`：通过，实际运行 `tests/test_business_data_integrity.py`。
- 新增回归：
  - `tests/test_business_data_integrity.py`
  - `tests/test_backend_integrity_followup.py`

## 未验证 / 阻塞项

- MySQL 集成测试未通过验收：

  ```bash
  /home/user/.venv/bin/python -m pytest tests/integration -o addopts='' -q
  ```

  结果：`37 errors`，根因是 `Can't connect to MySQL server on '127.0.0.1'`。
  这不是业务断言失败，而是环境缺少可用 MySQL；不能据此声明集成通过。

- PgSQL、Redis、Milvus、Dify、真实 SMTP/LLM 未在本环境做真实联调。
- 离线 59 个 skip 包含可选外部服务/真实联调用例，不计为集成验收通过。
- `files/audit` 等既有 501 占位模块仍按基线保留，未冒充真实实现。

## 后续验收建议

1. 准备独立 MySQL 测试库 `ai_talent_platform_test`，重跑 `tests/integration`。
2. 配置 PgSQL 后执行 `scripts/sync_analytics.py --full`，核对六张分析表与水位表。
3. 配置 Dify 工作流后验证 assign/score/analyze/report 的真实输出契约。
4. 配置 SMTP TLS/SSL 后验证邀约邮件真实发送与失败结果落库。
5. 配置 Milvus 后验证题库/人才向量覆盖写、权限过滤和删除。
