"""Fictional, local-only pool presentation checks."""
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from scout_agent.green_discovery import Job
from scout_agent.search import POLICY_VERSION, SearchEvaluation, SearchStore
from scout_agent.storage.db import Database
from scout_agent.web.app import create_app
from scout_agent.web.search_pool import PLATFORMS, STATUSES, VERDICTS, pool_url, query_pool, source_label


@pytest.fixture
def pool(tmp_path):
    path = tmp_path / 'fictional.db'
    with Database(path) as db:
        store = SearchStore(db)
        for i in range(63):
            platform = 'green' if i % 2 == 0 else 'forkwell'
            external = str(i + 1)
            identity = f'900001:{external}' if platform == 'green' else f'forkwell:{external}'
            url = (f'https://www.green-japan.com/company/900001/job/{external}' if platform == 'green'
                   else f'https://jobs.forkwell.com/jobs/{external}')
            job = Job(identity, url, {'title': f'Fictional job {i}'}, ['AWS'] if platform == 'green' else ['求人一覧'],
                      platform, f'900001:{external}' if platform == 'green' else external)
            # Green retains the historical identity convention.
            if platform == 'green':
                job.platform = None
                job.external_job_id = None
            store.save_job(job)
            store.save_result(job, POLICY_VERSION, SearchEvaluation(
                verdict=('TARGET', 'POSSIBLE', 'DROP')[i % 3], summary='虚构摘要'), 'mock')
            if i % 5 == 0:
                store.set_user_state(identity, 'APPLIED')
            db.conn.execute('UPDATE search_jobs SET last_seen_at=? WHERE job_id=?',
                            (None if i % 7 == 0 else f'2026-10-{i % 28 + 1:02d}', identity))
        db.conn.commit()
    return path


def test_facets_and_combinations(pool):
    with Database(pool, read_only=True) as db:
        store = SearchStore(db, migrate=False)
        rows, states = store.current_results(), store.user_states()
    def match(item, s, v, p):
        job, result = item
        return (states.get(job.job_id, 'ACTIVE') in s
                and result.verdict in v
                and (job.platform or 'green') in p)
    for status in (('ACTIVE',), ('APPLIED',), ('EXCLUDED',), ('ACTIVE','APPLIED'), STATUSES):
        for verdict in (('TARGET',), ('TARGET','POSSIBLE'), ('POSSIBLE','DROP'), VERDICTS):
            for platform in (PLATFORMS, ('green',), ('forkwell',), ('doda',), ('green','forkwell')):
                data = query_pool(pool, status, verdict, platform, 1)
                assert data['total'] == sum(match(row, status, verdict, platform) for row in rows)
                assert len(data['results']) == min(data['total'], 20)
                for key, counts in data['facets'].items():
                    for value, count in counts.items():
                        dims = dict(status=status, verdict=verdict, platform=platform)
                        dims[key] = (value,)
                        assert count == sum(match(row, *dims.values()) for row in rows)


def test_sort_pagination_and_legacy(pool):
    all_rows = []
    for page in range(1, 5):
        data = query_pool(pool, STATUSES, VERDICTS, PLATFORMS, page)
        assert len(data['results']) == (20 if page < 4 else 3)
        all_rows.extend(data['results'])
    with sqlite3.connect(pool) as conn:
        timestamps = dict(conn.execute('SELECT job_id,last_seen_at FROM search_jobs'))
    expected = sorted(all_rows, key=lambda row: row[0].job_id)
    expected.sort(key=lambda row: timestamps[row[0].job_id] or '', reverse=True)
    expected.sort(key=lambda row: ('TARGET', 'POSSIBLE', 'DROP').index(row[1].verdict))
    assert all_rows == expected
    with sqlite3.connect(pool) as conn:
        conn.execute('DROP TABLE search_job_user_state')
        conn.execute('ALTER TABLE search_jobs DROP COLUMN last_seen_at')
        before = conn.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    assert query_pool(pool, ('ACTIVE',), VERDICTS, PLATFORMS, 1)['total'] == 63
    with sqlite3.connect(pool) as conn:
        assert conn.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == before


def test_http_filters_links_dom_and_boundaries(pool):
    app = create_app(pool, search_runner=lambda *args: pytest.fail('no runner'))
    with TestClient(app) as client:
        html = client.get('/search').text
        assert html.count('<article class="search-row">') == 20
        default = query_pool(pool, ('ACTIVE',), ('TARGET','POSSIBLE'), PLATFORMS, 1)
        assert f'共 {default["total"]} 条' in html
        for params in ({'status':'bad'}, {'verdict':'bad'}, {'platform':'bad'}, {'page':0}, {'page':-1}, {'page':'bad'}, {'page':'1.5'}, {'page':'1.0'}, {'page':'+1'}, {'page':' 1 '}):
            assert client.get('/search', params=params).status_code == 422
        response = client.get(pool_url(STATUSES, VERDICTS, PLATFORMS, 999), follow_redirects=False)
        assert response.status_code == 303
        assert parse_qs(urlsplit(response.headers['location']).query)['page'] == ['4']
        html = client.get('/search?status=APPLIED&verdict=TARGET&platform=green').text
        assert '<form action="/search" method="get"' in html
        assert 'name="page" value="1"' in html
        assert 'name="status" value="APPLIED" checked' in html
        assert 'name="verdict" value="TARGET" checked' in html
        assert 'name="platform" value="green" checked' in html
        assert 'verdict=TARGET&amp;verdict=POSSIBLE' in html
        assert 'value="ALL"' not in html and 'value="RECOMMENDED"' not in html
        assert '<details><summary>查看详细统计</summary>' in html
        assert 'value="AWS" checked>AWS 相关职位' in html
        assert 'input.value = value' in html
        assert 'document.createTextNode(sourceLabels[' in html
        assert 'if (response.ok) { location.reload(); return; }' in html
        response = client.get('/search?platform=doda&page=9', follow_redirects=False)
        assert response.status_code == 303
        assert 'page=1' in response.headers['location']
        assert client.get('/search?platform=doda&page=1').status_code == 200


@pytest.mark.parametrize('platform,value,label', [
    ('green','Terraform','Terraform 相关职位'), ('green','インフラエンジニア','インフラエンジニア职种'),
    ('doda','インフラエンジニア','インフラエンジニア职种'), ('mynavi','インフラエンジニア','インフラエンジニア职种'),
    ('forkwell','求人一覧','全部求人'), ('lapras','求人検索','求人搜索首页'),
    ('findy','おすすめ求人','推荐求人'), ('type','DevOps・SRE','DevOps・SRE（职种入口）'),
])
def test_source_labels(platform, value, label):
    assert source_label(platform, value) == label


def test_state_change_reload_normalizes_last_page(pool):
    app = create_app(pool, search_runner=lambda *args: pytest.fail('no runner'))
    with TestClient(app) as client:
        # APPLIED has 13 rows initially; populate one extra page locally.
        with Database(pool) as db:
            store = SearchStore(db, migrate=False)
            jobs = [job for job, result in store.current_results()
                    if store.user_states().get(job.job_id, 'ACTIVE') == 'ACTIVE']
            for job in jobs[:8]:
                store.set_user_state(job.job_id, 'APPLIED')
        url = pool_url(('APPLIED','EXCLUDED'), VERDICTS, PLATFORMS, 2)
        data = query_pool(pool, ('APPLIED','EXCLUDED'), VERDICTS, PLATFORMS, 2)
        assert data['total'] == 21 and len(data['results']) == 1
        job_id = data['results'][0][0].job_id
        assert client.get(url).text.count('<article class="search-row">') == 1
        response = client.post(f'/search/jobs/{job_id}/state', json={'status':'ACTIVE'},
                               headers={'X-CSRF-Token':app.state.search_runs.csrf_token})
        assert response.status_code == 200
        reloaded = client.get(url, follow_redirects=False)
        assert reloaded.status_code == 303
        assert parse_qs(urlsplit(reloaded.headers['location']).query) == {
            'status':['APPLIED','EXCLUDED'], 'verdict':list(VERDICTS), 'platform':list(PLATFORMS), 'page':['1']}
        for platform in ('green', 'forkwell', 'lapras', 'findy', 'type', 'doda', 'mynavi'):
            assert client.get('/search', params={'platform':platform}).status_code == 200


@pytest.mark.parametrize('dimension', ['status', 'verdict', 'platform'])
def test_empty_invalid_and_presets(pool, dimension):
    with TestClient(create_app(pool, search_runner=lambda *args: pytest.fail('no runner'))) as client:
        for value in ('', 'ALL', 'RECOMMENDED', 'invalid'):
            assert client.get('/search', params={dimension: value}).status_code == 422
        assert client.get('/search', params={dimension + '_present': '1'}).status_code == 422


def test_repeated_query_normalization(pool):
    with TestClient(create_app(pool, search_runner=lambda *args: pytest.fail('no runner'))) as client:
        response = client.get('/search?status=APPLIED&status=ACTIVE&status=ACTIVE'
                              '&verdict=POSSIBLE&verdict=TARGET&platform=forkwell&platform=green&page=999',
                              follow_redirects=False)
        assert response.status_code == 303
        query = parse_qs(urlsplit(response.headers['location']).query)
        assert query['status'] == ['ACTIVE', 'APPLIED']
        assert query['verdict'] == ['TARGET', 'POSSIBLE']
        assert query['platform'] == ['green', 'forkwell']
        html = client.get(response.headers['location']).text
        assert html.count('<article class="search-row">') <= 20
        assert 'if (response.ok) { location.reload(); return; }' in html
