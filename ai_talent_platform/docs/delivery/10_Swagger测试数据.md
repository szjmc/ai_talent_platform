# Swagger 测试数据

> 服务：`http://127.0.0.1:8010`　文档：`http://127.0.0.1:8010/docs`
> 数据前置：岗位 `1`「后端开发工程师」（已确认画像）、人才 `1~5`（张伟 / 李娜 / 王强 / 赵敏 / 陈晨）。

---

## 0. 先拿 Token（所有业务接口都要）

`POST /api/v1/auth/login`，无需鉴权。

```json
{
  "username": "admin",
  "password": "Admin@12345"
}
```

取响应里的 `data.access_token`，点 Swagger 右上角 **Authorize** 粘贴（只填 token 本身，不要带 `Bearer `）。有效期默认 2 小时。

---

## 1. JD 解析（生成画像草稿，不落库）

`POST /api/v1/positions/1/jd/parse`

```json
{
  "jd_text": "岗位职责：\n1. 负责公司核心业务系统的后端服务开发，参与需求评审与技术方案设计；\n2. 负责接口性能优化，保障系统稳定性；\n3. 参与技术文档编写与代码评审。\n\n任职要求：\n1. 本科及以上学历，计算机相关专业，3 年以上后端开发经验；\n2. 熟练掌握 Python，熟悉 FastAPI 框架；\n3. 熟悉 MySQL 数据库设计与优化；\n4. 有 Redis 缓存使用经验者优先；\n5. 薪资 25k-35k，具体面议。",
  "job_title": "后端开发工程师"
}
```

预期：`channel=rule_fallback`（未配 Dify/LLM 时的真实降级）、`required_skills=[Python, FastAPI, MySQL]`、`preferred_skills=[Redis]`、`min_education=本科`、`min_work_years=3`、`salary_range=25k-35k`。

> 「薪资 25k-35k」不会再被当成技能；「熟悉 MySQL 数据库设计与优化」只算 MySQL，不额外造「数据库设计」硬条件。

---

## 2. 确认画像（生成新版本，匹配的前置条件）

`POST /api/v1/positions/1/profile/confirm`

先 `GET /api/v1/positions/1/profile` 看当前 `version`（本文档编写时为 8），把它填进 `expected_version`。

```json
{
  "profile": {
    "job_title": "后端开发工程师",
    "responsibilities": [
      "1. 负责公司核心业务系统的后端服务开发，参与需求评审与技术方案设计；",
      "2. 负责接口性能优化，保障系统稳定性；",
      "3. 参与技术文档编写与代码评审。"
    ],
    "required_conditions": [],
    "preferred_conditions": [],
    "required_skills": ["Python", "FastAPI", "MySQL"],
    "preferred_skills": ["Redis"],
    "min_education": "本科",
    "min_work_years": 3,
    "location": null,
    "salary_range": "25k-35k",
    "missing_fields": [],
    "conflicts": [],
    "review_required": true,
    "source": "manual",
    "prompt_version": "rule-v1"
  },
  "expected_version": 8,
  "note": "HR 确认：必备 Python/FastAPI/MySQL，Redis 仅加分"
}
```

预期：返回 `version = 旧版本 + 1`、`confirmed=true`。
验证并发保护：把 `expected_version` 故意改成 `1`，应返回 **400**「画像版本已变化（期望 1，当前 N），请刷新后重试」。

> 不想管版本号时，去掉 `expected_version` 字段即可（不做乐观并发校验）。

---

## 3. 执行人岗匹配（核心）

`POST /api/v1/matchings/run`

```json
{
  "position_id": 1,
  "top_k": 3,
  "min_score": 0,
  "strategy": "hybrid",
  "with_explanation": true
}
```

只测指定的人（跳过全库召回）：

```json
{
  "position_id": 1,
  "talent_ids": [1, 3, 5],
  "top_k": 10,
  "with_explanation": false
}
```

预期（当前数据）：

| rank | 姓名 | 分数 | decision | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 张伟 | 99.5 | PASS | 三项硬条件全 PASS |
| 2 | 陈晨 | 98.0 | PASS | 命中优先技能 Redis |
| 3 | 王强 | 24.22 | NEEDS_REVIEW | 档案几乎为空，UNKNOWN 降权但**不淘汰** |

`excluded` 里应有 2 条：李娜（缺 fastapi/mysql，命中 1/3）、赵敏（大专<本科、2<3 年、缺 mysql）。

> `strategy=soft` 只排序不淘汰（`excluded=[]`），但仍给出四态明细与 `stats.unknown`。
> `with_explanation=false` 表示不调外部模型，规则解释仍会生成（零成本）。

---

## 4. 回读最新结果

`GET /api/v1/matchings/positions/1`（无 body）

预期 `batch_id` 与第 3 步一致，`items` 的 `rank` / `decision` / 排序完全一致，`excluded` 不丢失。

---

## 5. 匹配记录分页

`GET /api/v1/matchings?page=1&page_size=5`

可选过滤：`position_id=1`、`talent_id=1`、`min_score=60`。

预期：`{items, total, page, page_size, pages}`，每条带 `talent_id` / `talent_name` / `position_title`。

---

## 6. 某人才的历史匹配

`GET /api/v1/matchings/talents/1`（无 body）→ 返回该人跨岗位的历史记录，按时间倒序。

---

## 7. 匹配解释（含证据校验）

`POST /api/v1/matchings/explain`

```json
{
  "position_id": 1,
  "talent_id": 1
}
```

预期：`highlights` 3 条（每条挂 `evidence_ids: ["EVID-1-1-1"]` 等）、`source=rule_fallback`、`rejected_claims=[]`、`pending_verification=[]`、`summary` 含「满足 3 项 / 不满足 0 项」。

被淘汰的人也能解释：`talent_id=2` → `summary` 会显示「满足 2 项、不满足 1 项」。

---

## 8. 负向用例（验证契约，不需要改数据）

| 请求 | 期望 |
| --- | --- |
| `POST /matchings/run` 不带 token | 401 请先登录 |
| `{"position_id": 9999}` | 404 岗位不存在 |
| `{"position_id": 1, "requirement_version": 999}` | 404 岗位画像版本不存在 |
| `{"position_id": 1, "top_k": 0}` | 422 请求参数校验失败 |
| `{"position_id": 1, "min_score": 999}` | 422（分数上限 100） |
| `POST /positions/1/jd/parse` + `{"jd_text":"招后端"}` | 422（正文至少 20 字） |
| `{"position_id": 1, "talent_ids": [2, 4]}` | 200，`items=[]` + `excluded` 2 条（全员硬淘汰） |

---

## 9. curl 版（不走 Swagger 时）

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@12345"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

curl -s -X POST http://127.0.0.1:8010/api/v1/matchings/run \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"position_id":1,"top_k":3}'
```
