# 文件存储与 LLM 适配器使用手册（MinIO / 阿里云 OSS / DeepSeek）

> 标签：[架构组] [P1]
> 适用代码：`app/adapters/storage/*`、`app/infrastructure/storage/{s3_client,oss_client}.py`、
> `app/adapters/llm/{provider_config,deepseek_adapter}.py`、`app/ai/core/{model_selector,llm_manager}.py`
> 验收命令：`pytest tests/test_storage_and_llm_adapters.py -q`

本文说明两类**可插拔适配器**的配置方式与扩展方式，核心原则：
**业务代码不感知厂商，也不写死任何模型**——切换本地 / MinIO / OSS 只改 `.env`，切换 qwen / DeepSeek 也只改 `.env`。

---

## 一、文件存储适配器

### 1.1 统一契约

所有后端实现 `app/adapters/storage/base_storage.py` 的 `BaseStorage`：

| 方法 | 说明 |
| --- | --- |
| `save(data, filename, subdir="") -> str` | 保存并返回相对路径 / 对象键（入库用） |
| `read(path) -> bytes` | 读取文件内容 |
| `delete(path) -> bool` | 删除文件 |
| `exists(path) -> bool` | 是否存在 |
| `size(path) -> int` | 文件字节数 |
| `url(path) -> str` | 对外访问地址（公共读 / 反代 / CDN） |
| `presigned_url(path, expires_seconds=None)` | 临时签名地址（本地存储回退为 `url()`） |
| `health() -> dict` | 连通性 / bucket 自检（不抛异常） |
| `describe() -> dict` | 脱敏配置摘要 |

业务侧统一获取方式：

```python
from app.adapters.storage.base_storage import get_storage

storage = get_storage()                # 取 settings.STORAGE_BACKEND（默认 local）
storage = get_storage("minio")         # 显式指定后端
key = await storage.save(data, "简历.pdf", subdir="resume")
url = storage.presigned_url(key, 600)  # 前端直链下载（对象存储）
```

对象键与本地布局完全一致：`{biz_type}/{yyyy}/{mm}/{uuid}{ext}`，
因此从本地迁移到 MinIO / OSS 不需要改数据库里的 `files.file_path` 语义。

### 1.2 后端与注册名

| 注册名 | 实现文件 | 底层客户端 | 签名方式 |
| --- | --- | --- | --- |
| `local` | `local_storage_adapter.py` | `infrastructure/storage/file_storage.py` | 无 |
| `minio` | `minio_adapter.py` | `infrastructure/storage/s3_client.py` | AWS Signature V4 |
| `oss` | `oss_adapter.py` | `infrastructure/storage/oss_client.py` | 阿里云 OSS Signature V1 |
| `s3` | `s3_storage_adapter.py` | `infrastructure/storage/s3_client.py` | AWS Signature V4 |

> 未知的后端名会记录 warning 并回退 `local`；已知后端导入失败、密钥等配置缺失则在初始化阶段抛
> `ExternalServiceError`，避免静默降级导致文件落到错误位置，也便于启动自检尽早暴露问题。

### 1.3 MinIO 配置

```bash
STORAGE_BACKEND=minio
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_SECURE=false
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-talent
MINIO_REGION=us-east-1
# 可选：公共读 / CDN 域名
MINIO_PUBLIC_URL=
```

本地一键拉起（`docker-compose.yml` 已内置 `storage` profile）：

```bash
docker compose --profile storage up -d
# 控制台 http://127.0.0.1:9001（minioadmin / minioadmin）
```

若桶未预先创建，可调用：

```python
from app.adapters.storage.minio_adapter import MinioStorageAdapter
await MinioStorageAdapter().ensure_bucket()
```

MinIO 固定使用 path-style（`http://host:9000/bucket/key`），预签名地址由 SigV4 生成，默认有效期
`STORAGE_PRESIGN_EXPIRE_SECONDS=3600`。

### 1.4 阿里云 OSS 配置

```bash
STORAGE_BACKEND=oss
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_SECURE=true
OSS_ACCESS_KEY_ID=your-ak
OSS_ACCESS_KEY_SECRET=your-sk
OSS_BUCKET=ai-talent
# 可选：CDN 域名
OSS_PUBLIC_URL=
```

- 访问方式为 virtual-hosted：`https://{bucket}.{endpoint}/{key}`；
- 支持 `presigned_url()`（`OSSAccessKeyId` + `Expires` + `Signature`），适合前端直传 / 限时下载；
- 使用最小权限 RAM 子账号，仅授予目标 bucket 的 `PutObject / GetObject / DeleteObject`。

通用 S3 兼容存储（AWS S3 / 其他网关）：

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT=s3.ap-southeast-1.amazonaws.com
S3_SECURE=true
S3_PATH_STYLE=false          # AWS 用 virtual-hosted，自建网关一般用 true
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET=ai-talent
S3_REGION=ap-southeast-1
S3_PUBLIC_URL=
```

### 1.5 接入新后端（示例：腾讯云 COS / 七牛）

1. 在 `infrastructure/storage/` 增加底层客户端（或复用 S3 协议客户端）；
2. 新建 `app/adapters/storage/xxx_adapter.py`：

```python
@storage_registry.register("cos")
class CosStorageAdapter(ObjectStorageAdapter):
    name = "cos"
    backend = "cos"

    def __init__(self, **kwargs):
        self.public_url = ""
        self.client = CosClient(...)   # 实现 put/get/head/delete/presigned_url/health
```

3. 在 `base_storage._ADAPTER_MODULES` 注册模块路径，`.env` 增加对应配置即可，业务代码零改动。

---

## 二、LLM 适配器（DeepSeek 及其他模型）

### 2.1 设计：模型与厂商都不写死

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 供应商目录 | `adapters/llm/provider_config.py` | 默认地址 / 默认模型 / 专属配置项 / 模型命名特征 |
| 协议适配器 | `openai_adapter.py` / `deepseek_adapter.py` / `qwen_adapter.py` / `ollama_adapter.py` | 只做协议翻译 |
| 模型策略 | `ai/core/model_selector.py` | 按用途 + 供应商选模型、温度、降级链 |
| 统一门面 | `ai/core/llm_manager.py` | 实例缓存、对话 / 流式 / 向量、token 埋点、降级 |

选模型优先级（高 → 低）：

1. 环境变量按用途覆盖：`LLM_MODEL_<PURPOSE>`，供应商级 `LLM_MODEL_<PROVIDER>_<PURPOSE>`；
2. `LLM_MODEL_MAP` 显式映射：`chat=deepseek-chat,talent=deepseek-reasoner`（或 JSON）；
3. 供应商专属模型（如 `DEEPSEEK_MODEL`、`OPENAI_MODEL`），其次是 `LLM_MODEL`（与当前供应商匹配且不是目录默认值 → 视为显式指定）；
4. 供应商目录中的「用途推荐模型」；
5. `LLM_MODEL`（与供应商匹配时）；
6. 供应商目录默认模型。

因此 `LLM_PROVIDER=deepseek` 时即使 `.env` 里残留 `LLM_MODEL=qwen-plus`，也不会把 qwen 模型名发给 DeepSeek，
而是自动回落到 `deepseek-chat`（并记录到 `/api/v1/system/info` 的 `ai_ready` 中便于排障）。
同理，若 `LLM_BASE_URL` 填的是其他供应商的默认地址（例如残留的 DashScope 地址），
切换供应商后会被自动忽略；需要用代理 / 私有网关时请填写 `DEEPSEEK_BASE_URL` 等专属地址（或非默认的通用地址）。
`LLM_API_KEY` 同理只对当前供应商生效，跨供应商（如对话 DeepSeek、向量 qwen）必须配置各自的专属密钥变量。

#### OpenAI 兼容服务与完整模型 ID

`LLM_PROVIDER=openai` 也表示使用 OpenAI 兼容协议。配置了非默认的 `LLM_BASE_URL` 或 `OPENAI_BASE_URL` 时，显式模型可以是 `组织名/模型名` 或私有模型别名，不要求以 `gpt-` 开头。为兼容旧环境，通用模型若仍是其他厂商的目录默认值（如 `qwen-plus`），继续视为残留配置；若兼容端点确实使用这个别名，用 `OPENAI_MODEL` 或用途映射明确指定。原生 OpenAI 地址仍保留模型前缀检查；qwen、DeepSeek、Ollama 的原有推荐策略保持不变。Vanna 的 `sql`、`summary` 及其他用途统一走上述优先级，不在业务代码写死模型名。

管理器和直接创建适配器使用同一套地址/密钥解析：显式调用参数 > 供应商专属配置 > 当前供应商通用配置 > 目录默认值；非当前供应商不会借用通用地址或密钥。修改配置后重启服务（或由调用方明确清理实例缓存），不依赖修改环境变量自动更新已缓存实例。

#### 备用模型与兼容写法

原生默认模型保留原有降级链，使用标准 GPT 模型名的 OpenAI 代理也保留原有备用模型。兼容服务的自定义完整模型 ID 不根据品牌自动推导官方别名，只有显式配置的备用模型才会被尝试，且沿用同一服务地址和凭据；请确认备用模型在该端点可用。

`LLM_FALLBACK_MODELS` 保留原有常用厂商别名的斜杠写法，如 `qwen-max=qwen-plus/qwen-turbo`。带命名空间的模型使用 JSON 数组或竖线分隔：

```text
LLM_FALLBACK_MODELS={"org/main": ["Qwen/backup", "other/backup"]}
# 或
LLM_FALLBACK_MODELS=org/main=Qwen/backup|other/backup
```

未知的 `namespace/model` 按一个完整 ID 保留；旧配置若用 `/` 分隔多个自定义别名，存在语义歧义，应改用 `|` 或 JSON，不靠猜测拆分。配置 `qwen-plus=` 或 JSON 中的 `"qwen-plus": []` 可明确关闭该模型的备用链。400/401/403/404/422 等不可恢复 HTTP 错误直接返回，不反复重试或切换备用模型；408/409/429及5xx保留原有重试/降级行为。

### 2.2 DeepSeek 配置

```bash
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat          # 可换成 deepseek-reasoner 或任意新模型
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=               # 留空用 https://api.deepseek.com/v1
DEEPSEEK_MODEL=                  # 留空用 LLM_MODEL / 目录默认模型
```

按用途分别指定（推荐，成本与效果兼顾）：

```bash
LLM_MODEL_MAP=chat=deepseek-chat,talent=deepseek-reasoner,matching=deepseek-chat
# 或供应商限定写法
LLM_MODEL_MAP=deepseek.talent=deepseek-reasoner
```

代码中无需出现模型名：

```python
from app.ai.core.llm_manager import llm_manager

result = await llm_manager.chat(
    [{"role": "user", "content": "请给出人岗匹配建议"}],
    purpose="matching",
)
```

DeepSeek 特性处理：

- `deepseek-reasoner` 会返回 `reasoning_content`，适配器在 `content` 为空时回退使用；
- 推理模型不支持 `temperature / top_p / presence_penalty / frequency_penalty / logprobs`，
  适配器自动过滤，避免 400；
- DeepSeek **不提供 embedding**：`embed()` 会抛 `AIError` 并提示配置向量供应商，不会静默伪造向量。

### 2.3 对话用 DeepSeek、向量用 qwen（推荐组合）

```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-deepseek

LLM_EMBED_PROVIDER=qwen
LLM_EMBED_MODEL=text-embedding-v3
DASHSCOPE_API_KEY=sk-qwen
```

```python
vectors = await llm_manager.embed(["简历文本"])   # 自动走 qwen 向量模型
```

若未配置 `LLM_EMBED_PROVIDER`，调用 `embed()` 会得到明确错误提示（包含需要设置的环境变量名）。

### 2.4 其他供应商 / 自定义供应商

```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o-mini

# Ollama（本地无需密钥）
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:7b
```

新增厂商（如 Moonshot、自建 vLLM）：

1. 在 `provider_config.PROVIDER_SPECS` 增加一条目录（默认地址 / 默认模型 / 密钥配置项）；
2. 新建适配器（OpenAI 兼容协议可直接继承 `OpenAIAdapter`）：

```python
@llm_registry.register("moonshot")
class MoonshotAdapter(OpenAIAdapter):
    provider = "moonshot"
    default_model = "moonshot-v1-8k"
    default_base_url = "https://api.moonshot.cn/v1"
```

3. `LLM_PROVIDER=moonshot` 即可，`LLMManager` / Skills / Agent 无需改动。

### 2.5 模型降级链

```bash
# 追加 / 覆盖降级链（多个备选用 / 分隔）
LLM_FALLBACK_MODELS=deepseek-reasoner=deepseek-chat,deepseek-chat=deepseek-reasoner
```

未配置时按内置 `FALLBACK_CHAIN`，再退化到「同供应商目录内的备选模型」；
每次降级都会写 warning 日志，`LLMResult.model` 记录实际使用的模型，便于成本与效果回溯。

---

## 三、自检与排障

```bash
# 单元测试（离线，不依赖真实 MinIO / OSS / DeepSeek）
pytest tests/test_storage_and_llm_adapters.py -q

# 运行期自检
curl http://127.0.0.1:8000/api/v1/system/info | jq '.data | {storage, ai_ready}'
```

`ai_ready` 中可看到：

- `llm.provider` / `llm.default_model` / `llm.model_by_purpose` / `llm.embedding_provider`；
- `llm.providers[]`：每个内置供应商的当前地址、默认模型、密钥是否配置；
- `storage.backend` 与 `storage.target`（脱敏，不含密钥）。

```python
# 代码内自检
from app.adapters.llm.provider_config import describe_providers
from app.adapters.storage.base_storage import get_storage

print(describe_providers())
print(await get_storage().health())
```

常见问题：

| 现象 | 原因与处理 |
| --- | --- |
| `对象存储鉴权失败（HTTP 403）` | AK/SK 错误、bucket 权限不足、系统时间偏差超过 15 分钟 |
| MinIO 上传 404 / `NoSuchBucket` | bucket 未创建：调用 `ensure_bucket()` 或控制台新建 |
| DeepSeek 调用 400 `invalid_request_error` | 对 `deepseek-reasoner` 传了采样参数：改用 `purpose`，由适配器过滤 |
| `LLM 供应商 deepseek 不支持 embedding` / `未提供 embedding 模型` | 设置 `LLM_EMBED_PROVIDER=qwen`（或 openai）+ `LLM_EMBED_MODEL` |
| 切换供应商后仍用旧模型 | 检查 `LLM_MODEL` / `LLM_MODEL_MAP`；供应商不匹配的模型名会被忽略并在 `ai_ready` 中暴露 |
