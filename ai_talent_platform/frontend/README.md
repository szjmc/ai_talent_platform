# 智才云 TalentOS · 前端

企业级 AI 数字化人才平台前端。仅依赖后端公开 API，不包含也不修改任何后端业务代码。

## 技术栈

- React 19 + TypeScript + Vite
- Ant Design 5 + Ant Design Icons + TailwindCSS（关闭全局 preflight）
- Framer Motion（人才简历弱透视 CoverFlow、弹簧拖拽交互）
- ECharts（驾驶舱、人才画像、测评雷达、数据分析、中国人才热力地图）
- React Router、Axios（JWT 自动刷新、统一错误处理）
- 按页面懒加载、响应式布局

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

## 真实数据契约

- 前端不再包含 `mockData`、演示登录或 `VITE_DEMO_FALLBACK` 降级。
- 所有业务请求统一走 `{ code, message, data, trace_id }` 响应契约，页面只消费 `data`。
- 登录、用户、组织、人才、简历、测评、匹配、培训、知识库、数据分析、AI 助手、AI 数字人面试均对接后端真实接口。
- 后端返回占位/未实现时页面展示空态或错误提示，不使用模拟数据填充。
- 页面交互与既有字段命名保持不变；后端未返回的扩展字段以空态展示，不参与伪造统计。

## 页面与接口

| 页面 | 路径 | 后端接口 |
| --- | --- | --- |
| 登录 / 当前用户 / 修改密码 | `/login` `/profile` | `/auth/*` |
| 用户与权限 | `/users` | `/users/*` |
| 组织架构 | `/organizations` | `/organizations/*` |
| 人才档案 | `/talents` | `/talents/*`、`/organizations/tree`、`/resumes/*` |
| 岗位管理 | `/positions` | `/positions` 占位、`/positions/{id}/profile*` |
| 简历解析 | `/resumes` | `/resumes/*` |
| 人岗匹配 | `/matchings` | `/matchings/*` |
| 智能测评 | `/assessments` | `/assessments/*`、`/resumes/upload` |
| AI 数字人面试（HR） | `/ai-interviews` | `/ai-interviews/*` |
| AI 面试候选人页（公开，免登录） | `/ai-interview/:token` | `/ai-interviews/session/:token/*` |
| 培训发展 | `/trainings` | `/trainings/*` |
| 知识库 | `/knowledge` | `/knowledge/docs*`、`/text2answer`、`/conversations*` |
| 数据洞察 | `/analytics` | `/analytics/*` |
| AI 智能助手 | `/assistant` | `/agents/chat/stream`、`/agents/conversations*` |
| 文件中心 | `/files` | `/files` 占位 |
| 操作审计 | `/audit` | `/audit` 占位 |

## 后端启动

后端启动、数据库初始化和管理员创建方式见上级目录 `README.md`。前端不需要任何 Python 依赖，也不要求修改 FastAPI 配置。
