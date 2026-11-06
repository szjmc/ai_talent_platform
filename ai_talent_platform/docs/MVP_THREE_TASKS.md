# 三项优化与Swagger测试指南

## 发布前置条件

新增matching_batches快照表，必须先运行 scripts/migrate_matching_batches.py 查看DDL，确认后使用 --apply --database ai_talent_platform 应用到本地开发库，再重启服务。脚本只创建该表，不修改旧表，不回填虚构历史。生产环境禁止运行本脚本，交DBA评审SQL。
未执行迁移时，新批次保存和快照查询不可用。独立测试库已验证，不代表开发库已经升级。

## Swagger操作

1. 使用自己已有账号登录，Authorize仅填写access_token；不要提交Dify Key。
2. JD解析接口直接使用示例；确认接口外层必须是profile，不能粘贴整段响应data/message。
3. /matchings/run示例ID必须在当前数据库存在。每次执行都会新增历史批次，不是新增人才。
4. 返回batch_id后，GET /matchings/batches/{batch_id}可以查看本次快照，包括空结果。
5. GET /matchings/positions/{position_id}读取最新快照，即使本次无输出，也不会展示上次成功结果。
6. 老批次没有快照，岗位/人才原有查询兼容读取；新按批次接口对老批次返回404并说明。

## 错误识别

401：未登录或令牌失效；2003：用户名或密码不正确。
422 json_invalid：JSON标点/括号错误；其他422按errors字段修正。
400画像未确认：先解析并确认；404资源或画像版本不存在：核对当前库ID。
500 matching_batches不存在：尚未迁移，不能靠反复提交解决。
with_explanation=false使用规则解释；true失败也可能降级，必须看explanation_source。

## 解释校验

新增业务规则检查覆盖summary、highlights/gaps/risks、面试建议和待核实文本。
当前证据契约没有正式阈值配置，出现“阈值/及格线/合格线/录用概率”等规则表达会被拒绝，并保留rejected_claims。
普通完整度百分比仍可展示；违规总结替换为确定性计数。此校验不是全面语义证明，委婉改写仍需要人工抽检。
后端直连LLM提示词已补约束；Dify云端提示词本轮未修改，后端校验对所有通道生效。

## 测试分层

离线：pytest tests/unit --confcutdir=tests/unit（外部Key置空）。
隔离API回归：python scripts/verify_matching_api_isolated.py；创建唯一名称本地库，保留虚构数据，不删业务库。使用进程内ASGI而非外部HTTP进程，需另做部署后冒烟。
真实Dify：python scripts/check_matching_dify.py。生成JSON不等于业务校验通过，检查剔除记录。

## 预警限制

零输出预警使用最新快照并携带request_scope，表示本次评估范围，不代表全库没有合适人才。
非零储备不足仍根据输出记录人数计算，受top_k/min_score/指定人才影响，不能作为全库招聘决策。
本次未扩展评分维度、未修改其他Agent、未执行git提交推送。
