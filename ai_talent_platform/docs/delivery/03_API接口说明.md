# 交付物 4｜API 接口说明（人岗匹配链路）

> 只覆盖本次交付的 8 个接口。其他模块（auth/users/organizations）接口见 `docs/接口文档.md`。
> 所有响应统一为 `{code, message, data, trace_id}`；`code=200` 表示成功，非 200 时 `data` 为 `null`。
> 鉴权：`Authorization: Bearer <access_token>`。权限码见每节。

## 接口总览

| # | 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | POST | `/api/v1/positions/{position_id}/jd/parse` | `position:manage` | JD 智能解析（**草稿，不落库**） |
| 2 | POST | `/api/v1/positions/{position_id}/profile/confirm` | `position:manage` | 确认画像 → 生成新版本（**唯一生效入口**） |
| 3 | GET | `/api/v1/positions/{position_id}/profile` | `position:read` | 查询画像（含版本与确认状态） |
| 4 | POST | `/api/v1/matchings/run` | `matching:run` | 执行匹配（同步返回 + 落库） |
| 5 | GET | `/api/v1/matchings` | `matching:read` | 匹配记录分页 |
| 6 | GET | `/api/v1/matchings/positions/{position_id}` | `matching:read` | 岗位最近一次结果（从库还原，不重算） |
| 7 | GET | `/api/v1/matchings/talents/{talent_id}` | `matching:read` | 人才历史匹配记录 |
| 8 | POST | `/api/v1/matchings/explain` | `matching:read` | 单条解释（重算，不落库） |

---

## 1. JD 智能解析

`POST /api/v1/positions/{position_id}/jd/parse`

**请求**

```json
{
  "jd_text": "岗位职责：...任职要求：1. 本科及以上学历，3 年以上后端开发经验；2. 熟练掌握 Python，熟悉 FastAPI；3. 熟悉 MySQL 数据库设计与优化；4. 有 Redis 缓存使用经验者优先；5. 薪资 25k-35k。",
  "job_title": "后端开发工程师"
}
```

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `jd_text` | string | 是 | 最短 20 字符 |
| `job_title` | string | 否 | 与岗位档案不一致时写入 `conflicts` 提示人工核对 |

**响应（真实抓取）**

```json
{
  "code": 200,
  "message": "解析完成",
  "data": {
    "channel": "rule_fallback",
    "profile": {
      "job_title": "后端开发工程师",
      "responsibilities": ["负责公司核心业务系统的后端服务开发，参与需求评审与技术方案设计", "..."],
      "required_conditions": [],
      "preferred_conditions": [],
      "required_skills": ["Python", "FastAPI", "MySQL"],
      "preferred_skills": ["Redis"],
      "min_education": "本科",
      "min_work_years": 3,
      "salary_range": "25k-35k",
      "missing_fields": [],
      "conflicts": [],
      "review_required": true,
      "source": "manual",
      "prompt_version": "rule-v1"
    },
    "message": "解析完成"
  }
}
```

**关键约定**

- `channel` 只有 `dify` / `rule_fallback` 两个值，**如实反映实际通道**；未配置 Dify 时恒为 `rule_fallback`，`source=manual`（表示「规则/人工录入」，不是 AI 生成）。
- 解析结果**不写库**，必须再调确认接口才生效（防止「解析即生效」绕过 HR）。
- `min_education` / `min_work_years` 为 `null` 表示 JD 未提及，**不补默认值**，只进 `missing_fields`。
- 薪资、职责描述词不会被当成技能（见 `Bug 修复记录` MATCH-015）。

---

## 2. 确认岗位画像

`POST /api/v1/positions/{position_id}/profile/confirm`

**请求**

```json
{
  "profile": { "...": "把解析返回的 profile 原样回传，HR 可先修改" },
  "expected_version": 3,
  "note": "HR 确认"
}
```

**响应**

```json
{
  "code": 200,
  "message": "岗位画像 v4 已确认",
  "data": {
    "position_id": 1,
    "position_title": "后端开发工程师",
    "version": 4,
    "confirmed": true,
    "confirmed_by": 2,
    "confirmed_at": "2026-11-04T11:25:20",
    "profile": { "...": "..." }
  }
}
```

**关键约定**

- **乐观锁**：`expected_version` 与当前最新版本不一致 → `400 / code=1002`（实测报文：`画像版本已变化（期望 3，当前 4），请刷新后重试`）。
- 同一技能同时出现在 `required_skills` 与 `preferred_skills` → 拒绝确认（`conflicts` 非空则拦）。
- 确认后同步 `positions.requirements` 与 `skill_tags`，匹配链路只认**已确认版本**。

---

## 3. 查询岗位画像

`GET /api/v1/positions/{position_id}/profile?version=4`

`version` 可选（默认最新）。返回结构同上。实测：`version=3 confirmed=True confirmed_by=2`。
岗位无画像 → `404 / code=3001`。

---

## 4. 执行人岗匹配

`POST /api/v1/matchings/run`

**请求**

```json
{
  "position_id": 1,
  "talent_ids": [1, 2, 3, 4, 5],
  "top_k": 20,
  "min_score": 0,
  "strategy": "hybrid",
  "with_explanation": true,
  "requirement_version": null
}
```

| 字段 | 默认 | 约束 | 说明 |
| --- | --- | --- | --- |
| `position_id` | — | `>0` | 岗位必须有**已确认**画像，否则 `400` |
| `talent_ids` | null | 最多 200 | 为空则按状态 `active/reserve` 全库召回（上限 2000） |
| `top_k` | 20 | 1~200 | 只影响输出条数，不影响 `stats.candidates` 分母 |
| `min_score` | 0 | 0~100 | 低于该分不入结果（**不等于淘汰**，不进 `excluded`） |
| `strategy` | hybrid | — | 目前实现 `hybrid` 与 `score_only` 两套权重组合 |
| `with_explanation` | true | — | `false` 时**不调用外部模型**（Dify/LLM），仍生成规则解释；外部未配置时该项无实际差异 |
| `requirement_version` | null | ≥1 | 指定画像版本（复现历史结论用） |

**响应（真实抓取，节选）**

```json
{
  "code": 200,
  "message": "匹配完成",
  "data": {
    "batch_id": "26cdd04f036f4675b45893ce3eb47a58",
    "position_id": 1,
    "position_title": "后端开发工程师",
    "requirement_version": 5,
    "recall_mode": "sql_fallback",
    "matched_at": "2026-11-04T11:27:28",
    "stats": {"candidates": 2, "excluded_by_hard": 0, "unknown": 1, "needs_review": 1, "output": 2},
    "items": [
      {
        "talent_id": 1,
        "talent_name": "张伟",
        "rank": 1,
        "decision": "PASS",
        "score": 99.5,
        "dimension_scores": {"skill": 100.0, "experience": 100.0, "education": 100.0, "quality": 95.0},
        "defaulted_dimensions": [],
        "data_completeness": 1.0,
        "matched_skills": ["fastapi", "mysql", "python"],
        "highlights": ["min_education：学历满足：要求「本科」，实际「本科」"],
        "gaps": [],
        "hard_results": [
          {
            "item": "min_education",
            "required": "本科",
            "actual": "本科",
            "status": "PASS",
            "reason": "学历满足：要求「本科」，实际「本科」",
            "evidence_id": "EVID-1-1-1"
          },
          {
            "item": "min_work_years",
            "required": 3.0,
            "actual": 5.0,
            "status": "PASS",
            "reason": "工作年限满足：要求 3 年，实际 5 年",
            "evidence_id": "EVID-1-1-2"
          }
        ],
        "suggestion": "本次判定共 3 项硬性条件：满足 3 项、不满足 0 项 ...",
        "explanation_source": "rule_fallback"
      },
      {
        "talent_id": 3,
        "talent_name": "王强",
        "score": 24.22,
        "dimension_scores": {"skill": 0.0, "experience": 55.0, "education": 25.0, "quality": 70.0},
        "defaulted_dimensions": ["education", "skill", "experience", "quality"],
        "data_completeness": 0.33,
        "hard_results": [
          {"item": "min_education", "status": "UNKNOWN", "reason": "人才档案缺少学历信息，无法判断是否达到「本科」"},
          {"item": "min_work_years", "status": "NEEDS_REVIEW", "reason": "工作年限为 0，无法确认是应届生还是档案缺失（要求 3 年）"}
        ]
      }
    ],
    "excluded": [
      {
        "talent_id": 2,
        "talent_name": "李娜",
        "status": "FAIL",
        "reasons": ["required_skills：缺少必备技能：fastapi/mysql（命中 1/3）"]
      },
      {
        "talent_id": 4,
        "talent_name": "赵敏",
        "status": "FAIL",
        "reasons": [
          "min_education：学历不满足：要求「本科」，实际「大专」",
          "min_work_years：工作年限不满足：要求 3 年，实际 2 年",
          "required_skills：缺少必备技能：mysql（命中 2/3）"
        ]
      }
    ]
  }
}
```

**关键约定**

- `items` 只包含**未被淘汰**的人；`excluded` 只包含 **FAIL**；UNKNOWN / NEEDS_REVIEW 一律留在 `items` 里并标注。
- `stats` 是分母：`candidates = items + excluded_by_hard`（`min_score` 过滤会让人数小于该等式，属预期）。
- `matched_skills` 是事实（大写→小写归一），`highlights/gaps/suggestion` 是解释文本，两者不混用。
- `batch_id` 32 位 hex，同一次调用内所有明细共用；`stats.weights` / `stats.excluded` 随记录落库，便于事后复算与回读淘汰原因。

---

## 5. 匹配记录分页

`GET /api/v1/matchings?page=1&page_size=5`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `page` / `page_size` | int | 页码（默认 1）/ 每页条数（默认 20，上限 100） |
| `position_id` | int? | 按岗位过滤 |
| `talent_id` | int? | 按人才过滤 |
| `min_score` | float? | 最低综合分（0~100） |

> 以上三个过滤参数为 **MATCH-021 新增**：此前接口未声明它们，传了会被静默忽略并返回全表（`total` 与不加参数相同），已修正。


```json
{
  "code": 200,
  "message": "OK",
  "data": {
    "items": [
      {
        "batch_id": "ee0c0c6d0c814f17b2baec8f758088d0",
        "position_id": 1,
        "position_title": "后端开发工程师",
        "talent_id": 1,
        "talent_name": "张伟",
        "score": 99.5,
        "strategy": "hybrid",
        "requirement_version": 4,
        "matched_at": "2026-11-04T11:25:37",
        "explanation_source": "rule_fallback"
      }
    ],
    "total": 16,
    "page": 1,
    "page_size": 5,
    "pages": 4
  }
}
```

按 `matched_at DESC, id DESC` 排序；`talent_name` 为本轮补充字段（MATCH-016）。

---

## 6. 岗位最近一次结果

`GET /api/v1/matchings/positions/{position_id}`

返回结构**与 `/matchings/run` 完全一致**（含 `items`、`excluded`、`stats`），区别是全部从库中还原、不重新计算：

- 无记录 → `404 / code=3001`（`该岗位还没有匹配记录`）；
- `excluded` 的姓名与原因文本都能还原（本轮修复 MATCH-014）；
- 已知缺口：**全员淘汰的批次不落库**，此类批次回读会 404（见验收报告 P2-1）。

---

## 7. 人才历史记录

`GET /api/v1/matchings/talents/{talent_id}?limit=50`

```json
{
  "code": 200,
  "message": "OK",
  "data": [
    {
      "batch_id": "ee0c0c6d0c814f17b2baec8f758088d0",
      "position_id": 1,
      "position_title": "后端开发工程师",
      "talent_id": 1,
      "talent_name": "张伟",
      "score": 99.5,
      "strategy": "hybrid",
      "requirement_version": 4,
      "matched_at": "2026-11-04T11:25:37",
      "explanation_source": "rule_fallback"
    }
  ]
}
```

`limit` 越界（<1 或 >200）自动回落 50。只返回条目摘要，不返回整包证据（避免响应体膨胀）。

---

## 8. 生成匹配解释

`POST /api/v1/matchings/explain`

```json
{ "position_id": 1, "talent_id": 1, "requirement_version": null }
```

**响应（真实抓取）**

```json
{
  "code": 200,
  "message": "OK",
  "data": {
    "summary": "本次判定共 3 项硬性条件：满足 3 项、不满足 0 项、档案缺失 0 项、需人工复核 0 项；档案完整度 100%。（综合分 99.50）",
    "highlights": [
      {"text": "min_education：学历满足：要求「本科」，实际「本科」", "evidence_ids": ["EVID-1-1-1"]},
      {"text": "min_work_years：工作年限满足：要求 3 年，实际 5 年", "evidence_ids": ["EVID-1-1-2"]},
      {"text": "required_skills：必备技能全部命中（3/3）", "evidence_ids": ["EVID-1-1-3"]}
    ],
    "gaps": [],
    "risks": [],
    "interview_suggestions": [],
    "pending_verification": [],
    "rejected_claims": [],
    "source": "rule_fallback",
    "prompt_version": "rule-v1"
  }
}
```

**关键约定**

- 每条结论**必须**带 `evidence_ids`，否则被丢弃并进 `pending_verification`；被剔除的模型结论进 `rejected_claims` 并注明原因（含 `missing_evidence_misuse`）。
- `source` ∈ `dify` / `llm` / `rule_fallback`，当前环境恒为 `rule_fallback`（无密钥），**不谎称 AI 生成**。
- 即使该候选人已被硬条件淘汰也能调用（回答「他为什么没出现」）。

---

## 错误码（本次链路相关）

| HTTP | code | 触发场景 | 实测报文 |
| --- | --- | --- | --- |
| 401 | 2001 | 未带 / 无效 token | `请先登录` |
| 403 | 2004 | 权限不足（如员工调用 `matching:run`） | `没有操作权限` |
| 404 | 3001 | 岗位/人才不存在；岗位无匹配记录 | `岗位不存在（id=999999）` |
| 400 | 1002 | 画像未确认；版本冲突；岗位已关闭 | `请先确认岗位画像再执行匹配` / `画像版本已变化（期望 3，当前 4）` |
| 422 | 1001 | 参数校验失败（字段级明细） | `position_id must be > 0` |
| 502 | 5001/5002 | 外部服务（Dify/LLM）不可用 | 仅当显式启用外部通道时出现；否则降级不报错 |

> 说明：外部服务不可用时**不返回 5xx** —— 一律降级为规则通道并在 `channel`/`source` 中标注，避免前端把「降级」误读为「故障」。
