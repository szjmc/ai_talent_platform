# 岗位匹配 MVP：Dify接入与验收

不开发前端，使用 /docs 中岗位画像与匹配接口。保留现有算法及证据构建器。

## 配置

在仓库本地 .env 填写，密钥不要提交或发到聊天：

    DIFY_BASE_URL=https://你的Dify服务/v1
    DIFY_JD_API_KEY=JD应用密钥
    DIFY_EXPLANATION_API_KEY=解释应用密钥

两条工作流应发布为 Workflow 应用。旧 DIFY_API_KEY 作为兼容后备；不要以一个应用Key冒充两个不同工作流。输入分别 jd_text（文本）、evidence_pack（JSON字符串）。输出为 result（JSON字符串或对象），或直接多字段 outputs。

JD输出字段以 JobProfileParsed 为准：job_title、responsibilities、required_conditions、preferred_conditions、required_skills、preferred_skills、min_education、min_work_years、location、salary_range。未提及留空，不补造；Python负责缺失与冲突检查，HR仍需确认。
解释输出：summary；highlights/gaps/risks每项包含text、evidence_ids、claim_type；interview_suggestions数组。只引用证据包，不计算分数，不生成录用概率。evidence_ids必须来自输入。

## 运行验收

python scripts/check_matching_dify.py

仅发送虚构JD及虚构人才证据，不访问数据库；两条通道必须均返回dify，否则非零退出。Mock单测不算真实接通。先通过此命令，再验证API完整链路。

## API操作步骤

1. 登录获取令牌，在Swagger授权。
2. 选择已有岗位，调用JD解析，确认channel=dify；修订并确认画像。
3. 调用POST /api/v1/matchings/run，with_explanation=true；检查items.explanation_source=dify，权重与四态正确。
4. 查询岗位最新结果、人才历史，检查保存一致性。
5. 检查离职/软删人才不参与；未知字段不直接淘汰。

默认支持降级保证可用，但降级不满足“必须接通Dify”验收。真实服务Key/契约未配置时不要宣称完成。
数据库集成夹具含drop_all/create_all，只能在独立临时测试库执行，禁止使用业务库。全淘汰批次目前不保存，最新查询可能仍返回旧批次，这是未解决的交付限制；未擅自新增表。
