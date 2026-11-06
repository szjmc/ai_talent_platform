"""
模块：main.py（项目根目录 ai_talent_platform/main.py）
职责：项目启动入口。

标签：[架构组] [P0]（项目启动入口，被所有组依赖）

实现思路：
1. 只做「启动」这一件事：调用 core/application.create_application() 得到 app；
2. 直接 `python main.py` 或 `uvicorn main:app --reload` 均可启动；
3. 端口、host、日志级别全部来自 settings（环境变量 / .env）；
4. 生产环境推荐：`uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4`
   （多 worker 时注意：进程内限流/缓存不共享，需要 Redis）。

启动后：
    Swagger   http://127.0.0.1:8000/docs
    健康检查   http://127.0.0.1:8000/health
    接口根路径  http://127.0.0.1:8000/api/v1
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证以 `python main.py` 方式运行时能正确 import app 包
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.config.logging_config import get_logger, setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.core.application import create_application  # noqa: E402


setup_logging()
logger = get_logger("app.main")

# 供 uvicorn / gunicorn 引用的 ASGI 应用对象
app = create_application()


def main() -> None:
    """本地开发启动入口。"""
    import uvicorn

    logger.info("以开发模式启动：http://%s:%s%s", settings.HOST, settings.PORT, settings.DOCS_URL)
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        reload_dirs=[str(BASE_DIR / "app")],
        log_config=None,  # 使用项目自身的日志配置
        timeout_keep_alive=65,
    )


if __name__ == "__main__":
    main()
