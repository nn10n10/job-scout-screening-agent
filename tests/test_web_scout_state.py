"""Scout local states use fictional DBs; no browser/site/model calls."""
import sqlite3
from threading import Event

import pytest
from fastapi.testclient import TestClient

from scout_agent.web.app import create_app
from test_web import _seed_dashboard
from test_web_daily import wait_finished


def test_old_db_inline_details_and_readonly_get(tmp_path):
    path, ids = _seed_dashboard(tmp_path)
    before = path.read_bytes()
    with TestClient(create_app(path)) as client:
        html = client.get('/').text
        assert html.count('<details class="inline-detail">') == 2
        assert '<details class="inline-detail" open' not in html
        assert '展开详情' in html and '收起详情' in html
        assert 'fictional-codex' in html and 'AWS 基盘职责明确。' in html
        assert '第三项仅在详情页。' in html
        assert 'FICTIONAL PRIVATE' not in html
        assert '筛选详情</a>' not in html and '/jobs/' not in html
        assert client.get(f'/jobs/{ids["infra"]}').status_code == 200
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_user_states'").fetchone()


def test_local_state_transitions_filters_and_evaluation_unchanged(tmp_path):
    path, ids = _seed_dashboard(tmp_path)
    with sqlite3.connect(path) as conn:
        original = conn.execute('SELECT * FROM evaluations ORDER BY id').fetchall()
        scouts = conn.execute('SELECT * FROM scouts ORDER BY id').fetchall()
    app = create_app(path)
    headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
    endpoint = f'/jobs/{ids["infra"]}/state'
    with TestClient(app) as client:
        for status in ('APPLIED', 'EXCLUDED', 'ACTIVE', 'APPLIED'):
            response = client.post(endpoint, json={'status': status}, headers=headers)
            assert response.status_code == 200 and response.json() == {'status': status}
        assert 'Cloud Engineer' not in client.get('/').text
        html = client.get('/?status=ACTIVE&status=APPLIED&page_size=10').text
        assert 'Cloud Engineer' in html and 'SRE' in html
        assert '<strong id="summary-total">2</strong>' in html
        assert 'type 1' in html
        assert '恢复未处理' in html
        assert 'name="page" value="1"' in html
        assert 'name="status" value="APPLIED" checked' in html
        assert 'Cloud Engineer' in client.get('/?status=APPLIED').text
        assert 'Cloud Engineer' not in client.get('/?status=EXCLUDED').text
        assert client.post('/jobs/999999/state', json={'status': 'ACTIVE'}, headers=headers).status_code == 404
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM evaluations ORDER BY id').fetchall() == original
        assert conn.execute('SELECT * FROM scouts ORDER BY id').fetchall() == scouts


@pytest.mark.parametrize('query', ['status=', 'status=ALL', 'status=active', 'status=ACTIVE&status=bad', 'status_present=1'])
def test_invalid_status_filter(tmp_path, query):
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        assert client.get('/?' + query).status_code == 422


@pytest.mark.parametrize('body', ['null', '[]', '"ACTIVE"', '{}', '{"status":"BAD"}', '{"status":[]}', '{"status":"ACTIVE","command":"foo"}', '{'])
def test_invalid_state_body_and_csrf(tmp_path, body):
    path, ids = _seed_dashboard(tmp_path)
    app = create_app(path)
    with TestClient(app) as client:
        endpoint = f'/jobs/{ids["infra"]}/state'
        assert client.post(endpoint, content=body).status_code == 403
        assert client.post(endpoint, content=body, headers={'x-csrf-token': app.state.daily_runs.csrf_token}).status_code == 422
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_user_states'").fetchone()


def test_daily_running_blocks_local_write(tmp_path):
    path, ids = _seed_dashboard(tmp_path)
    entered, release = Event(), Event()
    def runner(path, emit, platforms):
        entered.set()
        assert release.wait(5)
        return 0
    app = create_app(path, daily_runner=runner)
    headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
    with TestClient(app) as client:
        try:
            assert client.post('/api/daily/run', headers=headers, json={'platforms': ['green', 'type', 'doda', 'mynavi']}).status_code == 202
            assert entered.wait(1)
            assert client.post(f'/jobs/{ids["infra"]}/state', json={'status': 'APPLIED'}, headers=headers).status_code == 409
        finally:
            release.set()
        assert wait_finished(client)['state'] == 'completed'
        assert client.post(f'/jobs/{ids["infra"]}/state', json={'status': 'APPLIED'}, headers=headers).status_code == 200


def test_filtered_state_pool_sql_pagination_and_normalization(tmp_path, monkeypatch):
    from datetime import datetime
    from scout_agent.storage.db import Database
    from scout_agent.models.scout import Scout
    from scout_agent.models.evaluation import Evaluation
    from scout_agent.web import services
    path, ids = _seed_dashboard(tmp_path)
    with Database(path) as db:
        run = db.start_run('fictional-state-pagination')
        for n in range(24):
            sid = db.save_scout(Scout(id=f'state-{n}', platform='type', company_name='架空分页公司', job_title=f'Cloud {n}',
                jd_text='AWS 基盤の設計・構築を主担当。', received_on=datetime.now().date()))
            db.save_evaluation(sid, run, Evaluation(verdict='KEEP', confidence=.8, summary='虚构'), provider='codex', model_name='fictional')
        db.finish_run(run)
    app = create_app(path)
    headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
    loaded = []
    original = services._from_row
    def record(row):
        loaded.append(row['scout_id'])
        return original(row)
    monkeypatch.setattr(services, '_from_row', record)
    with TestClient(app) as client:
        client.post(f'/jobs/{ids["infra"]}/state', json={'status': 'EXCLUDED'}, headers=headers)
        url = '/?status=ACTIVE&status=APPLIED&provider=codex&platform=type&days=all&page_size=10'
        for size in (10, 20, 50, 100):
            loaded.clear()
            html = client.get(url.replace('page_size=10', f'page_size={size}')).text
            assert len(loaded) == min(size, 24)
            assert '<strong id="summary-total">24</strong>' in html
        loaded.clear()
        html = client.get(url).text
        assert len(loaded) == 10
        assert '<strong id="summary-total">24</strong>' in html and 'type 24' in html
        assert html.count('<details class="inline-detail">') == 10
        assert 'status=ACTIVE&amp;status=APPLIED' in html
        assert 'page_size=10' in html and 'provider=codex' in html
        loaded.clear()
        html = client.get(url + '&page=3').text
        assert len(loaded) == 4
        for sid in loaded[:]:
            client.post(f'/jobs/{sid}/state', json={'status': 'EXCLUDED'}, headers=headers)
        response = client.get(url + '&page=3', follow_redirects=False)
        assert response.status_code == 303
        assert 'page=2' in response.headers['location']
        assert 'status=ACTIVE&status=APPLIED' in response.headers['location']
        assert 'page_size=10' in response.headers['location']
