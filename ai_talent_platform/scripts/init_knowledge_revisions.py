"""仅创建knowledge_revisions；默认输出DDL，--apply才连接并建表。
标签：[RAG组] [P0]
"""
import os
os.environ.setdefault('USERNAME', 'Windows')
import argparse
import asyncio
from pathlib import Path
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects.mysql import dialect
from app.models.knowledge_revision import KnowledgeRevision


async def apply():
    from app.infrastructure.database.session import get_engine, close_db
    try:
        async with get_engine().begin() as connection:
            # 单表create，而不是metadata.create_all，不执行ALTER/DROP，不改其他表。
            await connection.run_sync(lambda sync: KnowledgeRevision.__table__.create(sync, checkfirst=True))
        print('knowledge_revisions 已存在或创建完成；其他表未修改')
    finally:
        await close_db()


async def check_references():
    from sqlalchemy import inspect
    from app.infrastructure.database.session import get_engine, close_db
    try:
        async with get_engine().connect() as connection:
            def read(sync):
                inspector = inspect(sync)
                for name in ['knowledge_docs', 'files', 'users']:
                    columns = inspector.get_columns(name)
                    primary = next(c for c in columns if c['name'] == 'id')
                    print(name, 'id:', repr(primary['type']), 'options:', inspector.get_table_options(name))
                if inspector.has_table('knowledge_revisions'):
                    name = 'knowledge_revisions'
                    print('NEW_TABLE_COLUMNS:', [c['name'] for c in inspector.get_columns(name)])
                    print('NEW_TABLE_FOREIGN_KEYS:', [(f['constrained_columns'], f['referred_table'], f['referred_columns']) for f in inspector.get_foreign_keys(name)])
                    print('NEW_TABLE_UNIQUE:', inspector.get_unique_constraints(name))
            await connection.run_sync(read)
    finally:
        await close_db()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--check', action='store_true', help='只读核对外键目标字段类型和引擎')
    parser.add_argument('--output', help='将DDL以UTF-8保存到指定文件，不连接数据库')
    args = parser.parse_args()
    if args.check:
        asyncio.run(check_references())
    elif args.apply:
        from sqlalchemy.exc import DBAPIError
        try:
            asyncio.run(apply())
        except DBAPIError as exc:
            # 只显示数据库原始错误，不输出连接凭证或整段DDL，便于定位建表失败。
            print('Database error:', exc.orig)
            raise SystemExit(1)
    else:
        table = KnowledgeRevision.__table__
        statements = [str(CreateTable(table).compile(dialect=dialect())) + ';']
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect())) + ';')
        ddl = '\n\n'.join(statements)
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ddl, encoding='utf-8')
            print('DDL exported')
        else:
            print(ddl)
