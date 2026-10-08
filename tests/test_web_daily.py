from threading import Event
import time

import pytest

from js_test_utils import require_node
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
    def runner(path, emit, platforms):
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
        assert client.post('/api/daily/run', headers=headers, json={'platforms': ['green', 'type', 'doda', 'mynavi']}).status_code == 202
        assert entered.wait(1)
        assert client.get('/api/daily/status').json()['state'] == 'running'
        assert client.post('/api/daily/run', headers=headers, json={'platforms': ['green', 'type', 'doda', 'mynavi']}).status_code == 409
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
    assert seen['argv'] == [sys.executable, '-u', '-m', 'scout_agent.web.daily_worker', str(db_path), 'green', 'type', 'doda', 'mynavi']
    assert seen['shell'] is False
    assert emitted == ['Daily verdicts: KEEP=1 MAYBE=0 SKIP=2']
    assert not db_path.exists()


def test_nonzero_daily_exit_is_failed(tmp_path):
    app = create_app(tmp_path / 'fictional.db', daily_runner=lambda path, emit, platforms: 1)
    with TestClient(app) as client:
        assert client.post('/api/daily/run', headers={'x-csrf-token': app.state.daily_runs.csrf_token}, json={'platforms': ['green']}).status_code == 202
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


@pytest.mark.parametrize('body', [
    {}, {'platforms': []}, {'platforms': ['unknown']}, {'platforms': 'green'},
    {'platforms': None}, None, [], {'platforms': ['green'], 'command': 'bad'},
    {'platforms': [1]}, {'platforms': [['green']]},
])
def test_daily_strict_body(tmp_path, body):
    app = create_app(tmp_path / 'fictional.db', daily_runner=lambda *args: 0)
    with TestClient(app) as client:
        headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
        assert client.post('/api/daily/run', headers=headers, json=body).status_code == 422
        assert client.post('/api/daily/run', headers=headers, content='{bad').status_code == 422


@pytest.mark.parametrize('selected,expected', [
    (['green'], ['green']), (['doda', 'green', 'doda'], ['green', 'doda']),
    (['mynavi', 'doda', 'type', 'green'], ['green', 'type', 'doda', 'mynavi']),
])
def test_selected_progress_and_restart(tmp_path, selected, expected):
    entered, release = Event(), Event()
    def runner(path, emit, platforms):
        assert list(platforms) == expected
        emit('Daily event: {"platform":"' + expected[0] + '","status":"running"}')
        entered.set()
        assert release.wait(3)
        for platform in platforms:
            emit('Daily event: {"platform":"' + platform + '","status":"completed"}')
        return 0
    app = create_app(tmp_path / 'fictional.db', daily_runner=runner)
    with TestClient(app) as client:
        headers = {'x-csrf-token': app.state.daily_runs.csrf_token}
        assert client.post('/api/daily/run', headers=headers, json={'platforms': selected}).status_code == 202
        assert entered.wait(1)
        state = client.get('/api/daily/status').json()
        assert state['selected_platforms'] == expected
        assert state['current_platform'] == state['stage'] == expected[0]
        assert state['platform_status'][expected[0]] == 'running'
        for platform in {'green', 'type', 'doda', 'mynavi'} - set(expected):
            assert state['platform_status'][platform] == 'skipped'
        release.set()
        state = wait_finished(client)
        assert all(state['platform_status'][p] == 'completed' for p in expected)
        app.state.daily_runs.runner = lambda path, emit, platforms: 0
        assert client.post('/api/daily/run', headers=headers, json={'platforms': ['type']}).status_code == 202
        assert wait_finished(client)['selected_platforms'] == ['type']


def test_daily_controls(tmp_path):
    from pathlib import Path
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        html = client.get('/').text
        controls = html.split('id="daily-platforms"')[1].split('</div>')[0]
        assert controls.count('type="checkbox"') == 4
        assert controls.count(' checked') == 4
        assert 'type="button" id="daily-all"' in controls
    js = Path('scout_agent/web/static/daily.js').read_text()
    preset = js.split("preset.addEventListener('click'")[1].split('let wasRunning')[0]
    assert 'fetch' not in preset and 'reload' not in preset
    assert 'JSON.stringify({platforms: selected()})' in js
    assert 'selected().length === 0' in js
    assert 'input.disabled = running || posting' in js
    assert 'preset.disabled = running || posting' in js


def test_daily_selection_sync_contract():
    """Keep the run identity contract covered even without a JS runtime."""
    from pathlib import Path
    js = Path('scout_agent/web/static/daily.js').read_text()
    sync = js.split('function render(data) {', 1)[1].split('latest = data;', 1)[0]
    assert 'let syncedRun;' in js
    assert 'if (data.selected_platforms?.length && syncedRun !== data.started_at) {' in sync
    assert 'input.checked = data.selected_platforms.includes(input.value);' in sync
    assert 'syncedRun = data.started_at;' in sync


def test_optional_node_missing_skips(monkeypatch):
    import js_test_utils
    monkeypatch.setattr(js_test_utils.shutil, 'which', lambda name: None)
    with pytest.raises(pytest.skip.Exception, match='Node unavailable'):
        require_node()


def test_daily_checkboxes_sync_once_per_run():
    from pathlib import Path
    import subprocess

    node = require_node()
    harness = r"""
const assert = require('node:assert/strict');
const element = () => ({disabled: false, textContent: '', dataset: {},
  listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; },
  replaceChildren() {}});
const checks = ['green', 'type', 'doda', 'mynavi'].map(value =>
  Object.assign(element(), {value, checked: true}));
const elements = Object.fromEntries(['daily-run', 'daily-status', 'daily-all',
  'daily-platform-status'].map(id => [id, element()]));
global.document = {getElementById: id => elements[id],
  querySelectorAll: () => checks, createElement: element};
let data = {state: 'idle', selected_platforms: [], started_at: null};
let nextPoll;
let reloads = 0;
let fetches = 0;
global.fetch = async () => { fetches++; return {ok: true, json: async () => data}; };
global.setTimeout = callback => { nextPoll = callback; };
global.window = {location: {reload() { reloads++; }}};
const selected = () => checks.filter(input => input.checked).map(input => input.value);
const flush = () => new Promise(resolve => setImmediate(resolve));
const poll = async update => { data = update; await nextPoll(); };
"""
    assertions = r"""
(async () => {
  await flush();
  assert.deepEqual(selected(), ['green', 'type', 'doda', 'mynavi']);
  const completed = {state: 'completed', started_at: 'fictional-run-1',
    selected_platforms: ['green'], platform_status: {
      green: 'completed', type: 'skipped', doda: 'skipped', mynavi: 'skipped'}};
  await poll(completed);
  assert.deepEqual(selected(), ['green']);
  assert(checks.every(input => !input.disabled));
  checks[2].checked = true;
  checks[2].listeners.change();
  await poll(completed);
  assert.deepEqual(selected(), ['green', 'doda']);
  const beforePreset = fetches;
  elements['daily-all'].listeners.click();
  assert.deepEqual(selected(), ['green', 'type', 'doda', 'mynavi']);
  assert.equal(fetches, beforePreset);
  await poll(completed);
  assert.deepEqual(selected(), ['green', 'type', 'doda', 'mynavi']);
  const running = {state: 'running', started_at: 'fictional-run-2',
    selected_platforms: ['doda']};
  await poll(running);
  assert.deepEqual(selected(), ['doda']);
  assert(checks.every(input => input.disabled));
  assert(elements['daily-all'].disabled);
  assert(elements['daily-run'].disabled);
  await poll({...running, state: 'failed'});
  assert(checks.every(input => !input.disabled));
  assert.equal(reloads, 1);
  checks[0].checked = true;
  await poll({...running, state: 'failed'});
  assert.deepEqual(selected(), ['green', 'doda']);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, '-e', harness + Path('scout_agent/web/static/daily.js').read_text() + assertions],
        capture_output=True, text=True, shell=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_progress_rejects_untrusted_output_and_retains_failure(tmp_path):
    from scout_agent.web.daily_runs import DailyRunManager
    manager = DailyRunManager(tmp_path / 'fictional.db')
    manager.state.update(state='running', selected_platforms=['green', 'doda'],
        platform_status={'green': 'idle', 'type': 'skipped', 'doda': 'idle', 'mynavi': 'skipped'})
    for line in [
        'Daily event: {"platform":"<script>","status":"running"}',
        'Daily event: {"platform":"type","status":"running"}',
        'Daily event: {"platform":"green","status":"secret-token"}',
        'Daily event: {"platform":"green","status":"running","raw":"secret"}',
        'Daily event: invalid', 'token=secret Traceback /private/db',
    ]:
        manager._emit(line)
    assert manager.snapshot()['current_platform'] is None
    manager._emit('Daily event: {"platform":"green","status":"running"}')
    assert manager.snapshot()['platform_status']['green'] == 'running'
    manager._emit('Daily event: {"platform":"green","status":"failed"}')
    manager._emit('Daily event: {"platform":"doda","status":"completed"}')
    manager._finish(False)
    state = manager.snapshot()
    assert state['platform_status'] == {'green': 'failed', 'type': 'skipped', 'doda': 'completed', 'mynavi': 'skipped'}
    assert state['current_platform'] is None
    assert state['safe_error'] == '筛选未完整完成，请在本机检查运行环境后重试。'


def test_worker_forwards_scope_and_structured_callback(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    from types import SimpleNamespace
    from scout_agent import cli, config
    db_path = tmp_path / 'fictional.db'
    seen = {}
    def main(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        kwargs['_daily_progress']({'platform': 'green', 'status': 'running'})
        return 0
    monkeypatch.setattr(cli, 'main', main)
    monkeypatch.setattr(config, 'load_settings', lambda _: SimpleNamespace(db_path=db_path))
    monkeypatch.setattr(sys, 'argv', ['daily_worker', str(db_path), 'doda', 'green', 'doda'])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module('scout_agent.web.daily_worker', run_name='__main__')
    assert exit_info.value.code == 0
    assert seen['_daily_platforms'] == ('green', 'doda')
    assert seen['argv'] == ['daily']
    assert capsys.readouterr().out == 'Daily event: {"platform": "green", "status": "running"}\n'


def test_waiting_and_unique_running_events(tmp_path):
    from scout_agent.web.daily_runs import DailyRunManager
    manager = DailyRunManager(tmp_path / 'fictional.db')
    manager.state.update(state='running', selected_platforms=['green', 'doda'],
        platform_status={'green': 'waiting', 'type': 'skipped', 'doda': 'waiting', 'mynavi': 'skipped'})
    manager._emit('Daily event: {"platform":"green","status":"running"}')
    manager._emit('Daily event: {"platform":"doda","status":"running"}')
    assert manager.snapshot()['platform_status']['green'] == 'waiting'
    assert manager.snapshot()['current_platform'] == 'doda'
    # An event for another platform must not clear the active platform.
    manager._emit('Daily event: {"platform":"green","status":"completed"}')
    assert manager.snapshot()['current_platform'] == 'doda'
    manager._emit('Daily event: {"platform":"doda","status":"waiting"}')
    assert manager.snapshot()['current_platform'] is None
    manager._finish(False)
    assert manager.snapshot()['platform_status']['green'] == 'completed'
    assert manager.snapshot()['platform_status']['doda'] == 'failed'
    from pathlib import Path
    assert "waiting: '等待后续处理'" in Path('scout_agent/web/static/daily.js').read_text()
