"""
隔离MySQL + 真实Dify + ASGI完整API栈；不删库、不连接业务库。

标签：[Agent组] [P2]（匹配接口隔离验证脚本）
"""
import os
import sys
import uuid
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = uuid.uuid4().hex[:12]
DB = 'matching_mvp_verify_' + RUN
os.environ.update(DB_HOST='127.0.0.1', DB_NAME=DB, PGSQL_ENABLED='false', REDIS_ENABLED='false', MILVUS_ENABLED='false', LLM_PROVIDER='qwen', LLM_API_KEY='', PYTHONDONTWRITEBYTECODE='1', LOG_DIR=str(Path('work/logs').resolve()), UPLOAD_DIR=str(Path('work/uploads').resolve()))
sys.path.insert(0, str(ROOT))
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine
from app.config.settings import settings
from app.models import Base
from app.models.organization import Organization
from app.models.position import Position
from app.models.talent import Talent
from app.models.user import User
from app.models.matching import MatchingRecord
from app.infrastructure.database.session import get_engine, get_session_factory, close_db
from app.core.application import create_application
from app.core.security import create_access_token
from httpx import AsyncClient, ASGITransport

events = []
def record(name, **values):
    events.append({'step': name, **values})
    print(name, json.dumps(values, ensure_ascii=False), flush=True)

async def main():
    # 1. 只允许本机和本次新库；CREATE不带IF NOT EXISTS，拒绝复用任何已有库。
    assert settings.DB_HOST == '127.0.0.1' and settings.DB_NAME == DB
    server = create_async_engine(settings.MYSQL_SERVER_URL)
    try:
        async with server.begin() as conn:
            await conn.execute(text(f'CREATE DATABASE `{DB}` CHARACTER SET utf8mb4'))
    finally:
        await server.dispose()
    record('isolated_database_created', database=DB)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 2. 仅准备虚构前置主数据；后续画像确认/匹配/查询全部经过HTTP路由。
    factory = get_session_factory()
    weights = {'skill': .6, 'experience': .2, 'education': .1, 'quality': .1}
    async with factory() as db:
        org = Organization(code='VERIFY-' + RUN, name='虚构验证部门', level=1, path='/')
        user = User(username='verify-' + RUN, password_hash='not-a-login-password', real_name='隔离验证用户', is_active=True, is_superuser=True)
        db.add_all([org, user]); await db.flush()
        pos = Position(title='Python后端工程师', code='VERIFY-' + RUN, organization_id=org.id, status='open', requirements={'weights': weights})
        good = Talent(name='虚构合适人才', education='本科', work_years=5, skill_tags=['Python', 'FastAPI'], potential_level='B', status='active')
        left = Talent(name='虚构离职人才', education='硕士', work_years=8, skill_tags=['Python', 'FastAPI'], potential_level='A', status='left')
        fail = Talent(name='虚构条件不符人才', education='大专', work_years=1, skill_tags=['Java'], status='active')
        db.add_all([pos, good, left, fail]); await db.commit()
        pid, uid, good_id, left_id, fail_id = pos.id, user.id, good.id, left.id, fail.id
    headers = {'Authorization': 'Bearer ' + create_access_token(uid)}
    app = create_application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://verify', headers=headers, timeout=180) as client:
        async def request(method, path, **kwargs):
            response = await client.request(method, '/api/v1' + path, **kwargs)
            assert response.status_code == 200, (path, response.status_code, response.text[:600])
            body = response.json(); assert body['code'] == 200
            assert body.get('trace_id')
            return body['data']
        # 3. 真实Dify解析，严格禁止把规则降级算成功。
        parsed = await request('POST', f'/positions/{pid}/jd/parse', json={'jd_text': '岗位名称：Python后端工程师\n岗位职责：开发后端API服务\n任职要求：本科及以上学历，3年以上工作经验，熟悉Python和FastAPI。'})
        assert parsed['channel'] == 'dify', parsed['channel']
        profile = parsed['profile']
        record('jd_parsed', channel=parsed['channel'], required_skills=profile['required_skills'])
        confirmed = await request('POST', f'/positions/{pid}/profile/confirm', json={'profile': profile, 'note': '虚构数据隔离验收'})
        assert confirmed['confirmed'] is True and confirmed['version'] == 1
        view = await request('GET', f'/positions/{pid}/profile')
        assert view['profile'] == confirmed['profile'] and view['version'] == 1
        record('profile_confirmed_and_read', version=1)
        # 4. 三人才点名评估：离职过滤，明确不符淘汰，合适人才必须走真实Dify解释。
        result = await request('POST', '/matchings/run', json={'position_id': pid, 'talent_ids': [good_id, left_id, fail_id], 'top_k': 5, 'strategy': 'hybrid', 'with_explanation': True, 'requirement_version': 1})
        assert [i['talent_id'] for i in result['items']] == [good_id], result
        assert result['stats']['candidates'] == 2
        assert fail_id in [i['talent_id'] for i in result['excluded']]
        assert result['weights_used'] == weights
        item = result['items'][0]
        assert item['explanation_source'] == 'dify' and item['rank'] == 1
        record('matching_api', batch_id=result['batch_id'], source=item['explanation_source'], score=item['score'], candidates=2, excluded_by_hard=result['stats']['excluded_by_hard'], weights=result['weights_used'])
        # 5. 新数据库会话证明请求已经commit，不只是读同一内存对象。
        async with factory() as db:
            rows = (await db.execute(select(MatchingRecord).where(MatchingRecord.batch_id == result['batch_id']))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert float(row.score) == item['score'] and row.requirement_version == 1
            assert row.explanation['source'] == 'dify'
            assert row.explanation['summary']
            record('committed_database_verified', records=1, explanation_source=row.explanation['source'], rejected_claims=len(row.explanation.get('rejected_claims', [])))
        latest = await request('GET', f'/matchings/positions/{pid}')
        assert latest['batch_id'] == result['batch_id']
        assert latest['items'][0]['score'] == item['score']
        assert latest['items'][0]['explanation_source'] == 'dify'
        assert latest['excluded'] == result['excluded']
        history = await request('GET', f'/matchings/talents/{good_id}')
        assert any(h['batch_id'] == result['batch_id'] and h['score'] == item['score'] for h in history)
        record('latest_and_history_verified', latest_batch=latest['batch_id'], history_count=len(history))
        # 6. 扩展接口逐项验证，某项失败后继续其他项，保留完整证据。
        failures = []
        async def check_case(name, fn):
            try:
                await fn()
                record(name, status='PASS')
            except Exception as exc:
                failures.append(name)
                record(name, status='FAIL', error=str(exc)[:1200])

        async def reverse_good():
            reverse = await request('POST', '/matchings/reverse', json={'talent_id': good_id, 'position_ids': [pid], 'with_explanation': False})
            assert len(reverse['items']) == 1 and reverse['items'][0]['position_id'] == pid, reverse
            assert reverse['items'][0]['score'] == item['score']

        async def reverse_fail():
            reverse = await request('POST', '/matchings/reverse', json={'talent_id': fail_id, 'position_ids': [pid], 'with_explanation': False})
            assert not reverse['items'] and reverse['excluded'], reverse

        warning_ids = []
        async def scan_and_repeat():
            payload = {'position_ids': [pid], 'shortage_threshold': 10, 'long_vacant_days': 90, 'auto_resolve': False}
            first = await request('POST', '/matchings/warnings/scan', json=payload)
            assert first['positions_scanned'] == 1 and first['created'] == 1, first
            assert len(first['items']) == 1 and first['items'][0]['warning_type'] == 'position_reserve_shortage', first
            warning_ids.append(first['items'][0]['id'])
            second = await request('POST', '/matchings/warnings/scan', json=payload)
            assert second['created'] == 0 and second['unchanged'] == 1, second
            assert second['items'][0]['id'] == warning_ids[0]

        async def list_warning():
            listing = await request('GET', f'/matchings/warnings?position_id={pid}&resolved=false&page=1&page_size=10')
            assert listing['total'] == 1 and listing['items'][0]['id'] == warning_ids[0], listing

        async def resolve_warning():
            assert warning_ids, 'scan did not return warning ID'
            wid = warning_ids[0]
            resolved = await request('POST', f'/matchings/warnings/{wid}/resolve', json={'note': '隔离测试人工解决'})
            assert resolved['resolved'] is True, resolved
            again = await request('POST', f'/matchings/warnings/{wid}/resolve', json={'note': '幂等重复解决'})
            assert again['resolved'] is True
            listing = await request('GET', f'/matchings/warnings?position_id={pid}&resolved=true')
            assert listing['total'] == 1

        async def automatic_resolution():
            high = await request('POST', '/matchings/warnings/scan', json={'position_ids': [pid], 'shortage_threshold': 10, 'auto_resolve': False})
            assert high['created'] == 1
            low = await request('POST', '/matchings/warnings/scan', json={'position_ids': [pid], 'shortage_threshold': 1, 'auto_resolve': True})
            assert low['resolved'] == 1 and not low['items'], low

        async def validation_and_auth():
            r = await client.post('/api/v1/matchings/warnings/scan', json={'shortage_threshold': 0})
            assert r.status_code == 422
            r = await client.post('/api/v1/matchings/reverse', json={'talent_id': 0})
            assert r.status_code == 422
            r = await client.post('/api/v1/matchings/warnings/999999/resolve', json={})
            assert r.status_code == 404
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://verify') as anonymous:
                for method, path, body in [('POST','/matchings/run',{'position_id':pid}), ('POST','/matchings/reverse',{'talent_id':good_id}), ('POST','/matchings/warnings/scan',{}), ('GET','/matchings/warnings',None), ('POST','/matchings/warnings/1/resolve',{})]:
                    kwargs = {'json': body} if body is not None else {}
                    r = await anonymous.request(method, '/api/v1'+path, **kwargs)
                    assert r.status_code == 401, (path, r.status_code)

        for name, fn in [('reverse_good',reverse_good), ('reverse_fail',reverse_fail), ('scan_idempotency',scan_and_repeat), ('warning_list',list_warning), ('manual_resolution_idempotency',resolve_warning), ('automatic_resolution',automatic_resolution), ('validation_401_422_404',validation_and_auth)]:
            await check_case(name, fn)
        async def zero_output_history():
            empty = await request('POST', '/matchings/run', json={'position_id':pid,'talent_ids':[fail_id],'with_explanation':False})
            assert not empty['items'] and empty['excluded']
            latest_empty=await request('GET',f'/matchings/positions/{pid}')
            assert latest_empty['batch_id']==empty['batch_id'] and not latest_empty['items']
            original=await request('GET',f"/matchings/batches/{result['batch_id']}")
            assert original['items'][0]['talent_id']==good_id
            historical_empty=await request('GET',f"/matchings/batches/{empty['batch_id']}")
            assert historical_empty['excluded']==empty['excluded']
            warning=await request('POST','/matchings/warnings/scan',json={'position_ids':[pid],'auto_resolve':True})
            assert any(i['warning_type']=='position_no_output' and i['batch_id']==empty['batch_id'] for i in warning['items'])
            # 高分阈值导致无输出，并不是硬淘汰；快照仍须正确保存。
            cutoff=await request('POST','/matchings/run',json={'position_id':pid,'talent_ids':[good_id],'min_score':100,'with_explanation':False})
            assert not cutoff['items'] and not cutoff['excluded']
            assert (await request('GET',f'/matchings/positions/{pid}'))['batch_id']==cutoff['batch_id']
        await check_case('zero_output_snapshot_history_and_warning',zero_output_history)
        assert not failures, failures
    record('PASS', scope='真实Dify+隔离MySQL+ASGI路由及鉴权；未启动外部HTTP进程', retained_database=DB)

async def run():
    try:
        await main()
    finally:
        await close_db()

if __name__ == '__main__':
    try:
        asyncio.run(run())
    finally:
        Path('matching-api-verification.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
