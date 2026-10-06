from threading import Event
import time

import pytest
from fastapi.testclient import TestClient

from scout_agent.web.app import create_app
from scout_agent.web.services import DashboardFilters, search_evaluations
from test_web import _seed_dashboard


def wait_finished(client):
    for _ in range(200):
        state = client.get('/api/daily/status').json()
        if state['state'] != 'running':
            return state
        time.sleep(.005)
    raise AssertionError('fake runner did not finish')


@pytest.mark.parametrize('failure', [False, True])
def test_daily_lifecycle_and_safe_failure(tmp_path, failure):
    entered, release = Event(), Event()
    def runner(path, emit):
        entered.set()
        assert release.wait(3)
        emit('Daily verdicts: KEEP=2 MAYBE=3 SKIP=4')
        emit('token=fictional-secret /private/db Traceback')
        if failure:
            raise RuntimeError('token=fictional-secret /private/db Traceback')
        return 0
    app = create_app(tmp_path / 'fictional.db', daily_runner=runner)
    headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
    with TestClient(app) as client:
        assert client.get('/api/daily/status').json()['state'] == 'idle'
        assert client.post('/api/daily/run').status_code == 403
        assert client.post('/api/daily/run', headers=headers).status_code == 202
        assert entered.wait(1)
        assert client.get('/api/daily/status').json()['state'] == 'running'
        assert client.post('/api/daily/run', headers=headers).status_code == 409
        release.set()
        state = wait_finished(client)
        assert state['state'] == ('failed' if failure else 'completed')
        assert state['latest_summary'] == {'KEEP': 2, 'MAYBE': 3, 'SKIP': 4}
        assert state['started_at'] and state['finished_at']
        assert 'Traceback' not in str(state) and 'fictional-secret' not in str(state)
        assert '/private/db' not in str(state)


@pytest.mark.parametrize('query', ['page=0', 'page=-1', 'page=1.5', 'page=abc', 'page=%2B1', 'page_size=11', 'page_size=0', 'page_size=010'])
def test_invalid_pagination(tmp_path, query):
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        assert client.get('/?' + query).status_code == 422


@pytest.mark.parametrize('size', [10, 20, 50, 100])
def test_pagination_scope_sql_and_links(tmp_path, size, monkeypatch):
    from scout_agent.models.scout import Scout
    from scout_agent.models.evaluation import Evaluation
    from scout_agent.storage.db import Database
    from datetime import datetime
    from scout_agent.web import services
    db_path, _ = _seed_dashboard(tmp_path)
    with Database(db_path) as db:
        run = db.start_run('fictional-pagination')
        for n in range(105):
            sid = db.save_scout(Scout(id=f'page-{n}', platform='type', company_name='架空分页社',
                job_title=f'Fictional Cloud {n}', jd_text='AWS 基盤の設計・構築を主担当。',
                received_on=datetime.now().date()))
            db.save_evaluation(sid, run, Evaluation(verdict='KEEP', confidence=.8, summary='虚构岗位'),
                               provider='codex', model_name='fictional')
        db.finish_run(run)
    loaded = []
    original = services._from_row
    def record(row):
        loaded.append(row['scout_id'])
        return original(row)
    monkeypatch.setattr(services, '_from_row', record)
    with TestClient(create_app(db_path)) as client:
        html = client.get(f'/?q=Fictional&platform=type&provider=codex&days=all&page_size={size}').text
        assert len(loaded) == size
        assert html.count('<article class="scout-row') == size
        assert '<strong id="summary-total">105</strong>' in html
        assert 'type 105' in html
        assert 'page=2' in html and f'page_size={size}' in html
        assert 'q=Fictional' in html and 'platform=type' in html and 'provider=codex' in html
        assert 'name="page" value="1"' in html
        first = loaded[:]
        loaded.clear()
        client.get(f'/?q=Fictional&provider=codex&days=all&page=2&page_size={size}')
        assert not set(first) & set(loaded)
        normalized = client.get('/?q=Fictional&page=999', follow_redirects=False)
        assert normalized.status_code == 303 and 'page=11' in normalized.headers['location']
        loaded.clear()
        default = client.get('/?q=Fictional')
        assert len(loaded) == 10
        assert '第 1 / 11 页' in default.text
    expected = search_evaluations(db_path, DashboardFilters(q='Fictional'), page_size=100)
    assert len(expected) == 100


def test_empty_page_normalization(tmp_path):
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        assert '共 0 条 · 第 1 / 1 页' in client.get('/').text
        response = client.get('/?page=2', follow_redirects=False)
        assert response.status_code == 303 and 'page=1' in response.headers['location']


def test_fixed_python_daily_runner(tmp_path, monkeypatch):
    import io
    import sys
    from scout_agent.web import daily_runs
    seen = {}
    class Process:
        stdout = io.StringIO('Daily verdicts: KEEP=1 MAYBE=0 SKIP=2\n')
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            return 0
    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return Process()
    monkeypatch.setattr(daily_runs.subprocess, 'Popen', popen)
    emitted = []
    db_path = tmp_path / 'data' / 'scouts.db'
    assert daily_runs.daily_runner(db_path, emitted.append) == 0
    assert seen['argv'] == [sys.executable, '-u', '-m', 'scout_agent.web.daily_worker', str(db_path)]
    assert seen['shell'] is False
    assert emitted == ['Daily verdicts: KEEP=1 MAYBE=0 SKIP=2']
    assert not db_path.exists()


def test_nonzero_daily_exit_is_failed(tmp_path):
    app = create_app(tmp_path / 'fictional.db', daily_runner=lambda path, emit: 1)
    with TestClient(app) as client:
        assert client.post('/api/daily/run', headers={'x-csrf-token': app.state.daily_runs.csrf_token}).status_code == 202
        assert wait_finished(client)['state'] == 'failed'


def test_idle_restores_only_recorded_daily_times(tmp_path):
    from scout_agent.storage.db import Database
    db_path = tmp_path / 'fictional.db'
    with Database(db_path) as db:
        run = db.start_run('daily')
        db.finish_run(run)
    before = db_path.read_bytes()
    with TestClient(create_app(db_path)) as client:
        state = client.get('/api/daily/status').json()
    assert state['state'] == 'idle'
    assert state['started_at'] and state['finished_at']
    assert state['latest_summary'] is None
    assert db_path.read_bytes() == before
