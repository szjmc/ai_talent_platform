# 合并冲突清单：test_merge_lz_xh ← origin/develop

> 生成时间：2026-11-06
> 合并方向：把 Gitee `develop` 分支（最新 `ac239d7`）合并进测试分支 `test_merge_lz_xh`
> 三方合并基点：`8bc6a31`
> 状态：**仅分析，未执行合并，未改动任何代码**

---

## 一、合并规模总览

| 维度 | 我方 test_merge_lz_xh | develop 侧 |
|------|----------------------|-----------|
| 相对基点改动文件 | 15 个 | 161 个 |
| 相对基点改动行数 | +2325 / -25 | +17048 / -899 |
| 核心内容 | Agent组：继任方案+周期报告+权限隔离+员工脱敏+本地缓存 | 前端整套 + RAG知识库/题库 + LangGraph调度 + NL2SQL超时 + 架构优化 + 大量脚本/测试 |

**结论**：体量悬殊。develop 侧是全平台大迭代，我方是 Agent 组局部功能。绝大部分文件是单方修改、可自动合并，真正的冲突集中在 **5 个双方都改过的文件**。

---

## 二、真正的冲突文件（5 个，双方都改）

`git merge-tree` 与「双方改动交集」一致，确认冲突文件为：

1. `app/services/agent_service.py`
2. `app/routers/agent_router.py`
3. `app/routers/router.py`
4. `app/core/middleware.py`
5. `app/ai/workflows/analysis_workflow.py`

---

## 三、逐文件冲突详情

### 🔴 冲突 1：app/services/agent_service.py（最严重）

**我方改动（+30 行）**
- `chat()` 方法：签名 `chat(self, agent_type: str, payload: dict, user)` → 返回 dict
- 内部：`set_context(user)` → `dispatch(agent_type, payload)` → 员工则 `sanitize_for_employee(result)`（脱敏）

**develop 侧改动（+625 行，整体重构）**
- `chat()` 方法：签名 `chat(self, body: AgentChatRequest, current_user)` → 返回 `AgentChatResponse`
- 新增：`_ChatSession`、`_should_use_dify`（Dify 分支）、`_chat_via_dify`、`prepare_chat`、`stream_events`（SSE 流式）、`_fallback_stream`、`list_conversations`、`list_messages`、`submit_task`、`get_task_status`、`_run_task` 等一整套会话/任务管理

**冲突点**
- `chat()` 方法**签名和返回值完全不一致**，无法自动合并，git 会直接标记冲突。
- 我方「员工脱敏」逻辑挂在**旧版 chat 的 dispatch 之后**；develop 新版 chat 走 Dify/流式分支，脱敏逻辑需要**重新找接入点**。

**解决方向（供参考）**
- 以 develop 新版 chat 为准，把「员工脱敏」逻辑移植到新版 chat 的**最终返回出口处**（对 `AgentChatResponse` 或流式前的最终文本做脱敏）。
- 需要确认新版 chat 里是否还调用 `dispatch`/`analytics_agent`，否则继任/周期报告取数链路也要跟着变。

---

### 🔴 冲突 2：app/routers/agent_router.py（严重）

**我方改动（+148 行，加 3 个接口）**
- `POST /chat`（非流式）：`require_permission(AGENT_USE)`
- `POST /report/period`（周期报告）：`require_roles("super_admin","admin","hr","manager")`
- `POST /succession`（继任方案）：`require_roles("super_admin","admin","hr","manager")`

**develop 侧改动（+153 / -32，整份重构）**
- 重写 chat，新增 SSE 流式 `chat_stream`、`list_conversations`、`list_messages`、`submit_task`、`get_task_status` 等 7 个接口
- 用 `AgentChatRequest` / `AgentChatResponse` 新 schema
- **完全没有**「继任方案」和「周期报告」这两个接口

**冲突点**
- 同一文件两份内容差异极大，`chat` 接口两边都有但实现不同。
- 我方的「继任方案 + 周期报告」是 develop **没有的独有功能**，必须保留。

**解决方向（供参考）**
- 整份文件以 develop 版为基础，**把我方的 `/report/period` 和 `/succession` 两个接口 + `require_roles` 权限**追加进去（develop 版没有这两个）。
- chat 接口以 develop 为准；确认 develop 版是否保留 `require_permission` 权限（它用了 `_AGENT_USE` 常量）。

---

### 🟡 冲突 3：app/routers/router.py（中等）

**我方改动**
- 把 knowledge / resume / text2answer / a_rag / rag 从「占位」改为**真实 include**
- 从 `_STUB_MODULES` 移除 knowledge / analytics / agents 占位

**develop 侧改动**
- include agent_router、talent_router、analytics_router
- 引入 `IMPLEMENTED_MODULES` 列表、`ApiResponse`、`scheduler.get_sync_status`
- **遗漏** knowledge / resume / text2answer / a_rag / rag 这 5 个我已 include 的
- `_STUB_MODULES` 里**残留** analytics / agents / knowledge 重复条目
- system/info 里出现**重复的 `implemented_modules` 键**（develop 自己写重了）

**冲突点**
- include 列表两边各加各的，行级冲突。
- develop 版自身还有 bug（`implemented_modules` 键重复、`_STUB_MODULES` 残留）。

**解决方向（供参考）**
- 取**并集**：我方的 5 个 include + develop 的 talent/analytics 都保留。
- 清理 `_STUB_MODULES` 残留项和重复的 `implemented_modules` 键。

---

### 🟢 冲突 4：app/core/middleware.py（最省心）

**我方改动（+4 / -1）**
- 仅 1 处：CORS `allow_credentials=True → False`，并加注释说明「Bearer Token 不依赖 Cookie，前端用通配符 CORS」

**develop 侧改动（+83 / -22）**
- CORS 改成「通配符模式**自动**关闭 allow_credentials」（注释明确写「Bearer 鉴权不受影响」）
- 新增 trace_id 白名单校验（防日志注入）、`clear_context()` 清理请求级 AI 上下文
- 限流改成 Redis 优先 + 进程内滑动窗口兜底

**冲突点**
- CORS 那几行两边都改了，但**意图一致**。

**解决方向（供参考）**
- **直接采用 develop 版**。develop 的「通配符自动关 credentials」已经等价覆盖了我方 `allow_credentials=False` 的需求，且更完整（还保留非通配符场景下 credentials 能力）。
- 我方 CORS 改动可放弃，避免冗余。

---

### 🟡 冲突 5：app/ai/workflows/analysis_workflow.py（中等）

**我方改动（+958 / -10）**
- 周期报告整套：`PERIOD_REPORT_TEMPLATE`、`PERIOD_TYPES`、`collect_data`、`build_charts`、`group_charts`、`detect_risks`、`build_insight_prompt`、`build_report`、`run_analysis` 等

**develop 侧改动（+237 / -20）**
- 另一套分析逻辑：`_current_context`、`_assess_risks`、`_render_insight`、`run_analysis_workflow` 等

**冲突点**
- 两边在同一文件加了**不同命名的函数**，冲突主要在文件头部 import 区和可能重叠的函数区域。

**解决方向（供参考）**
- 函数名基本不冲突，主要是**手工合并保留两边函数**。
- 我方的 `PERIOD_REPORT_TEMPLATE` 是周期报告接口 `/report/period` 的**依赖**，必须保留，否则周期报告会 import 报错。

---

## 四、单方修改文件（git 可自动合并，无需人工）

以下是我方独有的新文件，develop 没有，合并时**直接保留**：
- `app/services/desensitize.py`（员工脱敏）
- `app/services/report_cache.py`（本地缓存）
- `app/services/succession_rules.py`（继任规则）
- `app/services/succession_service.py`（继任服务）
- `app/ai/agents/analytics_agent.py`（+506 行，我方核心）
- `app/repositories/analytics_repository.py`

develop 侧独有的大批文件（前端、脚本、测试、RAG 题库等）合并时**自动带入**。

---

## 五、合并风险与建议

1. **核心难点**：develop 把 Agent 相关代码整体重构了（agent_service / agent_router 换了新架构），我方的「继任方案 + 周期报告 + 员工脱敏」需要**重新嫁接到新架构**，不是简单选边。

2. **依赖关系**：
   - 周期报告 `/report/period` 依赖 `analysis_workflow.PERIOD_REPORT_TEMPLATE`（我方）和 `agent_service.chat`（develop 重写版）
   - 继任方案 `/succession` 依赖 `succession_service`（我方，develop 没有）
   - 员工脱敏挂在新版 chat 出口

3. **建议合并顺序**：
   - 先解决 middleware（直接取 develop）
   - 再解决 router.py（取并集 + 清 bug）
   - 然后 agent_service / agent_router（以 develop 为底，移植我方 3 块功能）
   - 最后 analysis_workflow（保留两边函数）
   - 合并后必须**重新跑启动测试 + 权限/脱敏回归**（参考上一轮验证流程）

---

## 六、待你决策

- [ ] 合并策略：以 develop 为底移植我方功能？还是我功能优先？
- [ ] 员工脱敏在新版 chat（Dify/流式分支）里的接入点如何定？
- [ ] develop 自身 bug（router.py 重复键、_STUB_MODULES 残留）是否顺带修？
