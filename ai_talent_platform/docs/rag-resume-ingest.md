# RAG 上传 → 人才自动入库（链路说明）

> 更新时间：2026-11-08 ｜ 范围：`POST /files/upload`(category=resume) → 人才档案 → 画像/向量

## 1. 一句话结论

在知识库上传一份 `category=resume` 的原件，**原件与版本记录提交成功后**自动触发一次人才处理：
`revision_id → source_file_id → resume_id → talent_id`，同一 `talent_id` 在 MySQL 有画像、在 Milvus
的 `talent_vectors` 有一条复合画像向量（主键就是 `talent_id`）。预览、发布、恢复都不会再次触发。

## 2. 触发点（`app/routers/file_router.py`）

| 场景 | 提交时机 | 触发次数 |
| --- | --- | --- |
| 单文件上传 | 路由内先 `service.commit_upload()` 显式提交草稿，再排后台任务 | 1 次 |
| 多文件上传 | `upload_and_publish()` 内部逐份提交草稿；路由按每份 `revision_id` 各排一次 | 每份成功文件 1 次 |
| 预览 / 发布 / 恢复 / 编辑 | 不产生新原件 | **不触发** |

- 调度判断集中在 `app/services/resume_ingest.py::plan()`：非 `resume` 分类、无 `revision_id`、
  或当前用户没有 `resume:parse` 权限时一律不排任务，并在返回值的 `resume_ingest` 里写明原因。
- 后台任务只接收 `revision_id + user_id(+trace_id)`；角色在任务内从数据库重建
  （`load_context()`），**不接受请求体传入的角色**。

## 3. 人才入口（`app/services/talent_resume_service.py`）

- `process_uploaded_file(file_id)`：原有的「简历文件」入口（`biz_type=resume`）。
- `process_rag_revision(revision_id)`：知识库原件入口，只多做了入口校验
  （版本 `category=resume`、原件存在、原件上传人本人或超管），
  其余步骤与上面**同一份实现** `ingest_file()`：内容哈希秒传 → 读原件 → `parse_resume()` 工作流 →
  `finalize_parse()` 决定「建档案 / 待复核」→ 画像 + 向量。
- `storage_type=local` 与对象存储一视同仁（`FileService.read_original` 已支持本地回退）。

## 4. 派生资料与完成标准

`TalentService.refresh_derived()` 依次执行「画像 → 向量索引」，汇总状态：

| status | 含义 |
| --- | --- |
| `complete` | 画像与向量**都**成功 |
| `partial` | 其中一项成功，另一项失败/未启用（Milvus 未启用时属于此项） |
| `failed` | 两项都没成功 |

索引失败时**不删除档案**：`ensure_derived()` 只补缺失阶段（画像已存在就不重新生成，向量已是当前
版本就跳过），重试入口 `POST /talents/{id}/index/refresh`。是否需要重建由「Milvus 中 `talent_id`
读回的 `version` 是否等于 MySQL `talents.version`」决定，不额外维护索引状态表。

## 5. 向量 Schema 与读写一致性

- 集合 `talent_vectors`：`talent_id`(INT64, primary, auto_id=False) + `vector` ，
  动态字段 `version` / `name` / `kind="profile"` / `updated_at`；度量 COSINE。
- 写：`TalentRetrievalService.sync_index()` —— 一名人才一条复合画像文本向量，按 `talent_id` 覆盖写。
- 读：`retrieve()` 用 `filter="talent_id in [...]"` + 回表比对 `version`（旧版本向量直接丢弃）；
  `read_index()` / `detail()` 用 `query(filter="talent_id == N")` 读回状态。
- 若既有集合主键不是 `talent_id`，`create_collection()` 会直接报错而不是静默写入错误 Schema
  （这类集合在旧实现下本来也写不进去：字符串切片 ID 无法作为 INT64 主键）。

## 6. 回查接口

`GET /api/v1/talents/ingest/revisions/{revision_id}`（权限 `talent:read`）返回：

```
revision_id / category / revision_status / source_file_id / file_name / storage_type
resume_id / resume_status / talent_id / processing_status（succeeded|needs_review|failed|…）
duplicates[]  # 疑似重复候选，需人工确认，不自动合并
portrait{status,version} / index{status,version,indexed_version}
complete      # 画像与向量都就绪才为 true
```

## 7. 验收脚本

```bash
.venv/bin/python scripts/verify_rag_resume_ingest.py
```

用 SQLite（同一套 ORM）+ Milvus Lite（单文件）+ 确定性向量替身跑完整链路，逐条断言：

1. `revision_id → source_file_id → resume_id → talent_id` 可解析，`complete=true`；
2. 同一 `talent_id` 在库里有一条画像、在 Milvus 有一条同版本向量；
3. 再上传字节完全相同的文件 → 复用同一 `resume_id`/`talent_id`，不新增行；
4. 手机号命中的疑似重复 → `needs_review`，不自动绑定；
5. 索引故障时档案保留、返回 `partial/failed`，重试只补索引后恢复 `complete`。

> 真实环境验完整链路需要 LLM（结构化解析 + 画像）与 Embedding 供应商；只有知识库权限、
> 没有 `resume:parse` 的账号不会自动建模（原件照常保存，`resume_ingest.reason` 会说明原因）。
