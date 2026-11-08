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

---

## 8. 幂等与补扫（防止重复 / 防止漏掉）

| 场景 | 行为 |
| --- | --- |
| 同一份原件上传两次（字节相同） | 复用同一 `resume_id` / `talent_id`，只补缺失的派生阶段 |
| 同一 revision 被重复触发（双击、批量、多 worker） | 进程内 `_inflight` 集合 + `claim_revision()` 行锁去重，模型只调用一次 |
| 上次处理失败 | 允许重试（`claim_revision` 只在 succeeded/needs_review 时拒绝） |
| 进程重启导致后台任务丢失 | 恢复 worker 每 60s 调 `sweep()` 补扫（`RESUME_INGEST_SWEEP_ENABLED`，默认开）；也可手动 `POST /api/v1/talents/ingest/sweep` |
| 同内容简历属于其他账号 | 不静默复用，返回 409「相同内容的简历已由其他账号上传」；回查接口给 `needs_review` + `duplicate_of_other_owner` |

补扫判定完全基于数据状态，不引入任务表：`category=resume` + 有原件 + 状态不是
rejected/archived + 没有「succeeded/needs_review 的 ingest 记录」。

## 9. 删除 / 合并后的向量一致性

- `delete_talent()`：软删 MySQL 档案后按 `talent_id` 删除 Milvus 向量（失败只记日志，不回滚删除）。
- `merge()`：源档案软删后逐个删除其向量，主档 `version+1` 后重建，避免语义检索继续召回
  被合并掉的重复档案。

## 10. Milvus 集合结构要求（上线前必查）

`talent_vectors` 必须满足以下任一条，否则 `sync_index` 会给出**可执行**的报错而不是静默失败：

1. 开启动态字段（推荐，`scripts/init_milvus.py` 当前定义）；或
2. 静态 Schema 且已声明 `version` / `name` / `kind` / `updated_at`，且没有「必填但业务不写」的字段。

早期版本脚本建的是「严格静态 + organization_id/status/skill_tags 必填 + 未开动态字段」，
写入会报 `unexpected field version` / `Insert missed an field organization_id`。修复：

```bash
python scripts/init_milvus.py --replace talent_vectors --dim 1024   # 维度取 settings.EMBEDDING_DIM
```

同时校验向量维度：换 embedding 模型后维度不一致会被明确拒绝（同一条命令重建）。

## 11. 降级约定（为什么不会「整条链路挂掉」）

| 依赖 | 不可用时 |
| --- | --- |
| Milvus / Embedding | 画像照常生成；索引阶段标 `disabled`/`failed`，`status=partial`，可只补索引重试 |
| 语义查重 | 降级为「仅手机号查重」，不阻断简历入库 |
| 知识库发布时无 Embedding 密钥 | `RAG_ALLOW_PUBLISH_WITHOUT_EMBEDDING=true` 时发布为「仅词法检索」，`index_error` 明确提示 |
| LLM（简历结构化/画像） | 解析失败写 `parse_error`，`processing_status=failed`，原件与版本记录照常保留 |
