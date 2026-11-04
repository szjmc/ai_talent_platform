# 智才云 TalentOS · 前端

企业级 AI 数字化人才平台前端。仅依赖后端公开 API，不包含也不修改任何后端业务代码。

## 技术栈

- React 19 + TypeScript + Vite
- Ant Design 5 + Ant Design Icons
- ECharts（驾驶舱、人才画像、测评雷达、数据分析）
- React Router、Axios（JWT 自动刷新、统一错误处理）
- 按页面懒加载、响应式布局、演示数据降级

## 本地运行

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

访问 `http://localhost:5173`。开发服务器已将 `/api` 和 `/health` 代理到 `127.0.0.1:8000`，浏览器不会直接访问后端地址。

生产构建：

```bash
npm run build
npm run preview
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | API 基础路径，生产建议使用同源相对地址 |
| `VITE_DEMO_FALLBACK` | `true` | 后端未启动或模块返回 501 时使用演示数据；生产应设为 `false` |

## 页面与接口

| 页面 | 路径 | 后端接口状态 |
| --- | --- | --- |
| 登录 / 当前用户 / 修改密码 | `/login` `/profile` | 已对接 `/auth/*`，支持 access token 自动刷新 |
| 用户与权限 | `/users` | 已对接用户分页、创建、编辑、角色分配、启停、重置密码、删除 |
| 组织架构 | `/organizations` | 已对接组织树、创建、编辑、移动、删除 |
| 人才档案 | `/talents` | 按契约对接列表、CRUD、语义搜索；501 时演示 |
| 岗位管理 | `/positions` | 按契约对接 CRUD、状态流转；501 时演示 |
| 简历解析 | `/resumes` | 按契约对接 multipart 上传与异步解析 |
| 人岗匹配 | `/matchings` | 按契约对接匹配跑批与解释界面 |
| 智能测评 | `/assessments` | 按契约对接智能组卷，能力雷达与报告完整呈现 |
| 培训发展 | `/trainings` | 课程、培养计划、滞后预警与 AI 推课交互 |
| 知识库 | `/knowledge` | 按契约对接引用式 RAG 问答 |
| 数据洞察 | `/analytics` | 按契约对接 NL2SQL、图表解读与报告导出 |
| AI 智能助手 | `/assistant` | 按契约对接多轮 Agent 对话 |
| 文件中心 | `/files` | 按契约对接通用上传，鉴权下载与删除入口 |
| 操作审计 | `/audit` | 按契约实现日志筛选、Trace ID 与快照详情 |

## 演示与真实数据

- 未登录且允许演示回退时，默认进入带有「演示环境」标识的产品界面。
- 点击用户菜单退出后可进入登录页；登录页支持「进入产品演示」。
- 演示操作不会写入后端。真实登录后写操作严格按 API 契约发送。
- 后端尚未实现的 501 模块会展示「演示数据」标识，避免将样例数据误认为生产数据。

## 后端启动

后端启动、数据库初始化和管理员创建方式见上级目录 `README.md`。前端不需要任何 Python 依赖，也不要求修改 FastAPI 配置。
