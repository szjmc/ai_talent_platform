# 岗位人才匹配 Agent｜交付文档索引

> 交付日期：2026-11-04 ｜ 分支：`lz_agent`（未提交，`git diff` 可查）
> 结论速览：**主链路真实跑通；14 项需求 DONE 10 / PARTIAL 4；测试 183/183 通过；接口巡检 22/22；3 条外部依赖未接通已如实标注。**

## 文档导航

| # | 文档 | 内容 | 适合谁看 |
| --- | --- | --- | --- |
| 0 | [`index.html`](./index.html) | 一页式验收看板（数字 + 10 问 10 答 + 风险） | 汇报、演示 |
| 1 | [`01_需求追踪矩阵.md`](./01_需求追踪矩阵.md) | 14 项需求 × 状态 × 代码模块 × 差距 | 技术负责人、PM |
| 2 | [`02_环境与启动说明.md`](./02_环境与启动说明.md) | 环境基线、5 步启动、冒烟命令、常见问题 | 新接手的人 |
| 3 | [`03_API接口说明.md`](./03_API接口说明.md) | 8 个接口的请求/响应/错误码（含真实报文） | 前端、联调 |
| 4 | [`04_测试用例清单.md`](./04_测试用例清单.md) | 183 例清单，按业务红线归类 | QA、评审 |
| 5 | [`05_自动化测试报告.md`](./05_自动化测试报告.md) | 单元测试 144/144 | QA |
| 6 | [`06_集成测试报告.md`](./06_集成测试报告.md) | 集成/接口 34/34 + 跨模块回归 | QA、架构 |
| 7 | [`07_完整业务流程测试报告.md`](./07_完整业务流程测试报告.md) | 真实进程 + 真实库的端到端走查 | 业务方、验收人 |
| 8 | [`08_Bug修复与回归记录.md`](./08_Bug修复与回归记录.md) | 17 个问题：根因/影响/修复/回归 | 评审、复盘 |
| 9 | [`09_最终交付验收报告.md`](./09_最终交付验收报告.md) | 10 问 10 答 + L1~L5 结论 + 遗留风险 | 决策人 |
| 10 | [`10_Swagger测试数据.md`](./10_Swagger测试数据.md) | 可直接粘贴的请求体 + 预期结果 + 负向用例 | 联调、演示 |
| — | [`evidence/e2e_matching_run_20261104.log`](./evidence/e2e_matching_run_20261104.log) | 端到端原始执行日志 | 复核 |

## 三分钟上手

```bash
# 启动
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8010

# 验收（另开终端）
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" scripts/e2e_matching_check.py

# 接口巡检（22 项，快速排查「接口是否真的按契约返回」）
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" scripts/diagnose_api.py

# 测试
PYTHONPATH=. "D:/0904开班/.venv-match313/Scripts/python.exe" -m pytest tests/unit tests/integration -o addopts="" -q
```

## 必须如实转述的三句话

1. **解释与 JD 解析当前是规则通道**（`source=rule_fallback` / `channel=rule_fallback`），Dify 与 LLM 均无密钥，未做真实联调。
2. **召回是 SQL 过滤**（`recall_mode=sql_fallback`），Milvus 未部署。
3. **生产库还需执行一段 DDL**（新增 5 个可空列），脚本已备好，等 DBA 审批。
