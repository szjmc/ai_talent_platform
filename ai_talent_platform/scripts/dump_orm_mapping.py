"""导出全局 ORM 映射关系。

用法：
    python scripts/dump_orm_mapping.py                    # 控制台打印精简映射
    python scripts/dump_orm_mapping.py --format md        # 输出完整 Markdown 到 stdout
    python scripts/dump_orm_mapping.py --output docs/orm_mapping.md
    python scripts/dump_orm_mapping.py --format json --output orm_mapping.json

数据来源是 SQLAlchemy 元数据（app/models/mapping.py），不连接数据库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.models.mapping import (  # noqa: E402
    build_orm_mapping,
    print_orm_mapping,
    render_orm_mapping_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出全局 ORM 映射关系")
    parser.add_argument("--format", choices=["text", "md", "json"], default="text")
    parser.add_argument("--output", help="输出文件路径，不传则打印到 stdout")
    args = parser.parse_args()

    if args.format == "text":
        content = None
        if args.output:
            raise SystemExit("--format text 不支持 --output，请改用 md / json")
        print_orm_mapping()
        return

    content = render_orm_mapping_markdown() if args.format == "md" else json.dumps(
        build_orm_mapping(), ensure_ascii=False, indent=2
    )

    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"已写入：{path}")
        return
    print(content)


if __name__ == "__main__":
    main()
