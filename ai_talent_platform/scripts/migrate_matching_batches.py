"""
批次快照迁移：默认仅打印DDL；--apply需明确本机目标库，不删改旧表。

标签：[Agent组] [P2]（匹配批次表迁移脚本）
"""
import sys
import argparse
import asyncio
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import mysql
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from app.config.settings import settings
from app.models.matching import MatchingBatch

def ddl():
    table=MatchingBatch.__table__
    statements=[str(CreateTable(table).compile(dialect=mysql.dialect())).strip()+";"]
    statements.extend(str(CreateIndex(i).compile(dialect=mysql.dialect()))+";" for i in sorted(table.indexes,key=lambda x:x.name))
    return "\n\n".join(statements)

async def apply(database):
    if settings.DB_HOST not in ('127.0.0.1','localhost') or settings.APP_ENV in ('prod','production'):
        raise RuntimeError('仅允许本地开发或隔离测试环境')
    if database != settings.DB_NAME:
        raise RuntimeError('--database与当前配置不一致，拒绝操作')
    engine=create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.begin() as conn:
            def migrate(sync):
                if not inspect(sync).has_table('matching_batches'):
                    MatchingBatch.__table__.create(sync)
                columns={c['name'] for c in inspect(sync).get_columns('matching_batches')}
                expected=set(MatchingBatch.__table__.columns.keys())
                if not expected <= columns:
                    raise RuntimeError('现有同名表结构不兼容，请人工检查；未自动修改')
            await conn.run_sync(migrate)
        print('matching_batches已就绪；未修改其他表或旧数据')
    finally:
        await engine.dispose()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--database')
    args=parser.parse_args()
    if args.apply:
        if not args.database: parser.error('--apply必须指定--database')
        asyncio.run(apply(args.database))
    else: print(ddl())
