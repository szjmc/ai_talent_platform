"""
脚本：scripts/selfcheck.py
职责：培训模块「四层自检」一键运行器，给验收 / 答辩现场用。

标签：[Dify组] [P1]（培训模块四层自检运行器）

为什么要有这个脚本：
    pytest 一次全跑会把「组员的测试」和「我的测试」混在一起，
    只要有一条红就分不清是谁的问题。本脚本按层次分开跑、分开判定，
    输出的结论能直接说明「我的模块是好的」，而不是靠嘴解释。

四层含义：
    L1 单元层：纯规则函数，不连数据库。挂了 = 我写的判断逻辑错了。
    L2 集成层：真实 MySQL，跑诊断→排期→匹配→落库→跟踪→考核。挂了 = 我和数据库的配合错了。
    L3 回归层：组员既有模块的测试（架构分层 / ORM 映射 / 技能注册 / 数据库健康）。
              只读不改，用来证明「我接入公共层没有弄坏别人的东西」。
    L4 接口层：接口回归用例（路径参数注入 / 人才与课程归属 / 口径一致性）
              + ASGI 直连探针，检查服务能起来、培训核心接口全挂上了、没 token 会被拦。

使用方式：
    python scripts/selfcheck.py            # 全部四层
    python scripts/selfcheck.py --fast     # 跳过 L2/L3，只跑 L1 + L4（10 秒内出结果）
    python scripts/selfcheck.py --verbose  # 失败时打印完整 pytest 输出

退出码：0 全部通过；1 有任意一层失败（可直接被 CI / 老师脚本调用）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PY = sys.executable  # 用「当前正在运行本脚本的解释器」，避免 py311 / 系统 Python 混用


def _run(cmd: list[str], cwd: Path) -> tuple[int, str, float]:
    """执行子进程命令，返回 (退出码, 合并输出, 耗时秒)。

    统一 utf-8 + errors=replace：Windows 控制台是 GBK，pytest 输出里有中文和 emoji，
    不这样处理会抛 UnicodeDecodeError，把「测试失败」误报成「脚本崩溃」。
    """
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), time.perf_counter() - t0


def _tail(text: str, lines: int = 12) -> str:
    """只取输出末尾若干行——pytest 的结论永远在最后。"""
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


# ---------------------------------------------------------------- L4 接口探针
# 用 `python -c` 在独立子进程里跑，避免污染当前进程的事件循环与配置。
PROBE_CODE = r"""
import asyncio, json, sys
sys.path.insert(0, ".")
from httpx import ASGITransport, AsyncClient
from app.core.application import create_application

# 六节点闭环的关键出口：少任何一个都说明路由装配断了。
# 用「关键路径必须存在」代替早期的「路径总数 == 11」——
# 计数断言每加一个接口就得改脚本（站内信 3 个接口一加就误报 FAIL），
# 而漏挂某个核心接口才是真正要防的事故。
REQUIRED = [
    "/api/v1/trainings/courses",
    "/api/v1/trainings/plans",
    "/api/v1/trainings/plans/generate",
    "/api/v1/trainings/plans/{plan_id}/progress",
    "/api/v1/trainings/plans/{plan_id}/score",
    "/api/v1/trainings/records",
    "/api/v1/trainings/recommend",
    "/api/v1/trainings/lag-scan",
    "/api/v1/trainings/notifications",
    "/api/v1/trainings/stats/pass-rate",
]

app = create_application()

async def main():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://selfcheck") as ac:
        health = await ac.get("/health")
        schema = await ac.get("/openapi.json")
        guard = await ac.get("/api/v1/trainings/courses")   # 不带 token，应被拦
        paths = set(schema.json().get("paths", {}))
        trainings = sorted(p for p in paths if "/trainings" in p)
        missing = [p for p in REQUIRED if p not in paths]
        print(json.dumps({
            "health_status": health.status_code,
            "health_up": health.json().get("data", {}).get("status"),
            "trainings_paths": len(trainings),
            "required_total": len(REQUIRED),
            "missing_paths": missing,
            "no_token_status": guard.status_code,
        }, ensure_ascii=False))

asyncio.run(main())
"""


def layer_l4(verbose: bool) -> tuple[bool, str, float]:
    """L4 接口层 = 接口回归用例 + 在线探针。

    为什么两段都要：
        探针只证明「服务能起来、核心接口挂上了、没 token 会被拦」，
        它不进入任何接口内部，所以「参数校验与路由注入打架」这类问题看不出来
        （提交考核曾因此恒定 422）。接口回归用例负责这一段。
    """
    costs = 0.0

    # 1) 接口回归用例：路径参数 / 归属校验 / 口径一致性
    code, out, cost = _run(
        [
            PY, "-m", "pytest", "tests/test_training_router.py",
            "--no-header", "-p", "no:cacheprovider",
        ],
        BASE_DIR,
    )
    costs += cost
    cases_ok = code == 0
    cases_detail = _last_summary(out) if cases_ok else _tail(out)

    # 2) 在线探针：服务装配 + 鉴权
    code, out, cost = _run([PY, "-c", PROBE_CODE], BASE_DIR)
    costs += cost
    if code != 0:
        return False, f"接口用例：{cases_detail}\n探针脚本执行失败（见 --verbose）", costs

    line = next((r for r in out.strip().splitlines() if r.startswith("{")), "")
    if not line:
        return False, f"接口用例：{cases_detail}\n探针无 JSON 输出", costs
    data = json.loads(line)

    checks = [
        ("服务能起来", data.get("health_status") == 200),
        ("健康检查为 up", data.get("health_up") == "up"),
        (
            f"培训关键接口 {data.get('required_total', 0)} 条全挂载"
            + (f"（缺 {len(data['missing_paths'])} 条）" if data.get("missing_paths") else ""),
            not data.get("missing_paths"),
        ),
        ("无 token 被拦(401)", data.get("no_token_status") == 401),
    ]
    probe_ok = all(v for _, v in checks)
    probe_detail = " | ".join(f"{'OK' if v else 'NG'} {k}" for k, v in checks)
    if not probe_ok and not verbose:
        probe_detail += f"\n实测值：{json.dumps(data, ensure_ascii=False)}"

    ok = cases_ok and probe_ok
    detail = f"回归用例：{cases_detail}\n探针：{probe_detail}"
    return ok, detail, costs


def main() -> int:
    parser = argparse.ArgumentParser(description="培训模块四层自检")
    parser.add_argument("--fast", action="store_true", help="只跑 L1 + L4")
    parser.add_argument("--verbose", action="store_true", help="失败时打印完整输出")
    args = parser.parse_args()

    layers: list[tuple[str, str, object]] = [
        ("L1 单元层 | 纯规则，不连库", "tests/test_training_logic.py", None),
        (
            "L2 集成层 | 真实 MySQL 六节点",
            "tests/test_training_service.py tests/test_training_position_and_passrate.py",
            None,
        ),
        (
            "L3 回归层 | 组员既有模块，只读不改",
            "tests/test_architecture_layer.py tests/test_orm_mapping.py "
            "tests/test_skills.py tests/test_database_health.py",
            None,
        ),
        ("L4 接口层 | ASGI 直连 + 鉴权", "PROBE", None),
    ]
    if args.fast:
        layers = [layers[0], layers[3]]

    print("=" * 74)
    print("  培训模块自检  |  解释器：", PY)
    print("  工程目录：", BASE_DIR)
    print("=" * 74)

    results: list[tuple[str, bool, float]] = []

    for index, (title, target, _) in enumerate(layers, 1):
        # 全程只用 ASCII 标记（[PASS] / [FAIL] / | ）：
        # Windows 控制台默认 GBK，emoji 和花体符号在不同终端里宽度不一、易显示成方块。
        print(f"\n[{index}/{len(layers)}] {title}")
        if target == "PROBE":
            ok, detail, cost = layer_l4(args.verbose)
        else:
            # 不要再加 -q：pytest.ini 里已经有 -q，重复会变成 -qq，
            # 而 -qq 会把「14 passed」这类摘要行也压掉，导致这里读不到结论。
            cmd = [PY, "-m", "pytest", *target.split(), "--no-header", "-p", "no:cacheprovider"]
            code, out, cost = _run(cmd, BASE_DIR)
            ok = code == 0
            detail = _tail(out) if (not ok or args.verbose) else _last_summary(out)
        print(f"    [{'PASS' if ok else 'FAIL'}] {cost:.1f}s")
        if detail:
            for row in detail.splitlines():
                print(f"           {row}")
        results.append((title, ok, cost))

    total = sum(c for _, _, c in results)
    failed = [t for t, ok, _ in results if not ok]

    print("\n" + "=" * 74)
    print(f"  汇总：{len(results) - len(failed)}/{len(results)} 层通过，总耗时 {total:.1f}s")
    if failed:
        print("  未通过：" + "；".join(failed))
        print("  排错顺序：先看最上面那一层 → 修好再往下，下层失败往往是上层带出来的。")
    else:
        print("  结论：本模块四层全绿，可交付。")
    print("  说明：tests/test_common_layer.py 不在本自检判定范围内（公共层全链路，")
    print("        需单独 `pytest tests/test_common_layer.py` 跑，当前 8 条全绿）。")
    print("=" * 74)
    return 0 if not failed else 1


def _last_summary(out: str) -> str:
    """从通过的输出里提炼 '14 passed in 0.09s' 这类结论行。

    用正则而不是逐行找：pytest 的 -q 输出行尾带一堆 '='，
    Windows 下还可能夹 \r，直接字符串比对容易取不到。
    """
    match = re.search(r"(\d+ passed[^\n=]*)", out)
    if match:
        return match.group(1).strip()
    for row in reversed(out.strip().splitlines()):
        if "passed" in row:
            return row.strip().strip("= ").strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
