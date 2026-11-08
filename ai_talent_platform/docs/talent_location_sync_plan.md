# 人才地域数据同步方案

## 1. 背景

人才地图（TalentMap）需要按国家/地区聚合人才数量，但 `talents` 表最初未定义地域字段，导致地图无法显示人数。

本方案在 `talents` 表新增 5 个地域/职级字段，并通过 `sync_analytics.py` 同步到 PgSQL 分析库 `dim_talent_wide` 表，供前端人才地图和分析查询使用。

## 2. 字段定义

### MySQL `talents` 表（业务库）

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `country` | VARCHAR(64) | 国家 | 中国、美国、新加坡 |
| `country_code` | VARCHAR(8) | 国家代码（ISO 3166-1 alpha-2） | CN、US、SG |
| `province` | VARCHAR(64) | 省份/州 | 浙江省、加利福尼亚州 |
| `work_city` | VARCHAR(64) | 工作城市 | 杭州、深圳 |
| `job_level` | VARCHAR(32) | 职级 | P6、高级工程师 |

### PgSQL `dim_talent_wide` 表（分析库）

同步字段与 MySQL `talents` 表完全一致，通过 `sync_analytics.py` 写入。

## 3. 数据流架构

```
MySQL talents 表 (业务库)
    │
    │  1. 新增/编辑人才时直接写入地域字段
    │     (TalentCreate/TalentUpdate Schema + 前端表单)
    │
    ├──→ MySQL talents.country / province / work_city / ...
    │
    │  2. sync_analytics.py 定时/手动同步
    │     (build_dim_talent_wide_rows)
    │     - 优先使用 talents 表地域字段
    │     - 字段为空时从 current_company 推导 (derive_location)
    │
    └──→ PgSQL dim_talent_wide.country / province / work_city / ...
          │
          │  3. 前端查询 GET /analytics/talents/geo
          │     (AnalyticsService.talent_geo → AnalyticsRepository.talent_geo)
          │
          └──→ 前端 TalentsPage 合并到人才列表 → TalentMap 按国家聚合
```

## 4. 同步触发方式

### 4.1 自动同步（定时）

`sync_analytics.py` 可由外部调度器（如 crontab、Airflow）定时调用：

```bash
# 每日凌晨全量同步
0 2 * * * cd /path/to/app && python scripts/sync_analytics.py
```

### 4.2 手动同步

```bash
cd /path/to/app && python scripts/sync_analytics.py
```

### 4.3 增量同步策略

当前 `sync_analytics.py` 采用全量 upsert 策略（`ON CONFLICT (talent_id) DO UPDATE`），每次同步覆盖所有在职人才的地域数据。对于数据量大的场景，可后续改为增量同步（仅同步近期变更的人才）。

## 5. 数据迁移方案

### 5.1 新部署

`init_db.py` 的 `create_mysql_tables()` 通过 `Base.metadata.create_all` 自动创建包含地域字段的表结构，无需额外操作。

### 5.2 已有数据库（升级）

`init_db.py` 的 `migrate_talent_location_columns()` 函数自动补列（幂等）：

```bash
# 执行迁移（init_db 会自动调用）
cd /path/to/app && python scripts/init_db.py
```

迁移逻辑：
1. 检查 `talents` 表是否已存在地域列
2. 对不存在的列执行 `ALTER TABLE talents ADD COLUMN`
3. 已有列不重复添加

### 5.3 地域数据填充

迁移后地域字段默认为 NULL，可通过以下方式填充：

**方式一：前端编辑**
- 在 TalentsPage 的人才编辑表单中手动填写国家/省份/城市

**方式二：从 current_company 自动推导**
- `sync_analytics.py` 的 `derive_location()` 函数会从 `current_company` 字段中提取城市/国家关键词
- 同步到分析库时，talents 表字段为空的人才会自动使用推导结果
- 推导结果写入 `dim_talent_wide`，但不回写 `talents` 表

**方式三：批量导入**
- 通过 Excel/CSV 批量导入人才时附带地域信息

## 6. 降级策略

### 6.1 PgSQL 分析库未启用

当 `PGSQL_ENABLED=False` 时：
- `AnalyticsService.talent_geo()` 自动降级为 `AnalyticsFallbackRepository.talent_geo()`
- Fallback 路径从 MySQL 业务库直接查询 `talents` 表
- 地域字段为空时，从 `current_company` 实时推导（与 sync_analytics 口径一致）

### 6.2 地域字段全部为空

- TalentMap 的 `counts` 为空 → 地图显示空球体
- 不影响其他功能

## 7. 验证步骤

### 7.1 后端验证

```bash
# 1. 检查 talents 表地域列
mysql -e "SHOW COLUMNS FROM talents LIKE 'country';"
mysql -e "SHOW COLUMNS FROM talents LIKE 'province';"

# 2. 检查 dim_talent_wide 地域列
psql -c "\d dim_talent_wide" | grep country

# 3. 执行同步
python scripts/sync_analytics.py

# 4. 检查同步结果
psql -c "SELECT talent_id, country, province, work_city FROM dim_talent_wide WHERE country IS NOT NULL LIMIT 10;"

# 5. 验证 API 端点
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/analytics/talents/geo
```

### 7.2 前端验证

1. 打开 TalentsPage → 人才地图
2. 确认地图显示国家/地区分布和人数
3. 点击国家下钻到省级地图
4. 编辑人才档案，填写地域字段，保存后确认地图更新

## 8. 相关文件清单

| 文件 | 作用 |
|------|------|
| `app/models/talent.py` | Talent 模型新增 5 个地域字段 |
| `app/schemas/talent.py` | TalentCreate/TalentUpdate 新增地域字段 |
| `app/services/talent_profile_service.py` | public_data 返回地域字段 |
| `app/models/analytics.py` | DimTalentWide 新增地域字段 |
| `scripts/sync_analytics.py` | derive_location() + INSERT SQL 同步地域数据 |
| `scripts/init_db.py` | migrate_talent_location_columns() 列迁移 |
| `app/repositories/analytics_repository.py` | talent_geo() 查询方法（PgSQL） |
| `app/repositories/analytics_mysql_repository.py` | talent_geo() 查询方法（MySQL 降级） |
| `app/services/analytics_service.py` | talent_geo() 门面方法 |
| `app/services/analytics_statistics_service.py` | talent_geo() 统计服务方法 |
| `app/routers/analytics_router.py` | GET /analytics/talents/geo 端点 |
| `frontend/src/services/platform.ts` | analyticsApi.talentGeo() API 方法 |
| `frontend/src/pages/TalentsPage.tsx` | 获取地域数据并合并到人才列表 |
| `frontend/src/pages/TalentsPage.tsx` (表单) | 人才编辑表单新增地域输入框 |
| `frontend/src/components/talents/TalentMap.tsx` | 中文→英文国名映射 + 地域聚合 |
