"""Fictional local data only; no external services."""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scout_agent.green_discovery import Job
from scout_agent.search import SearchStore, SearchEvaluation, POLICY_VERSION
from scout_agent.storage.db import Database
from scout_agent.web.app import create_app
from scout_agent.web.viewmodels import search_cards


@pytest.fixture
def pool(tmp_path):
    path = tmp_path / 'fictional.db'
    with Database(path) as db:
        store = SearchStore(db)
        for index, verdict in enumerate(('TARGET', 'POSSIBLE', 'DROP'), 1):
            job = Job(f'900001:{index}', f'https://www.green-japan.com/company/900001/job/{index}?token=fictional',
                      dict(company='Fictional Company', title=f'Platform {index}', salary='600万円',
                           location='虚构地点' * 150, responsibilities='虚构职责', required='虚构要求',
                           preferred='虚构加分', technology='Terraform', remote='虚构远程'), ['AWS'])
            store.save_job(job)
            store.save_result(job, POLICY_VERSION, SearchEvaluation(
                verdict=verdict, summary='虚构摘要', reasons=['虚构理由'], concerns=['虚构风险']), 'mock')
    return path


def app_for(path):
    return create_app(path, search_runner=lambda *args: pytest.fail('runner must not run'))


def post(client, app, job='900001:1', status='APPLIED', **kwargs):
    return client.post(f'/search/jobs/{job}/state', json={'status': status},
                       headers={'X-CSRF-Token': app.state.search_runs.csrf_token}, **kwargs)


def test_old_table_get_no_migration(pool):
    with sqlite3.connect(pool) as conn:
        conn.execute('DROP TABLE search_job_user_state')
        before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    app = app_for(pool)
    with TestClient(app) as client:
        page = client.get('/search')
        assert page.status_code == 200
        assert '待处理（2）' in page.text
    with sqlite3.connect(pool) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_job_user_state'").fetchone() is None
    with TestClient(app) as client:
        assert post(client, app).status_code == 200


def test_api_validation_and_running(pool):
    app = app_for(pool)
    with TestClient(app) as client:
        for token in ('', 'wrong'):
            assert client.post('/search/jobs/900001:1/state', json={'status':'APPLIED'},
                               headers={'X-CSRF-Token':token}).status_code == 403
        headers = {'X-CSRF-Token':app.state.search_runs.csrf_token}
        for payload in ({'status':'bad'}, {}, [], {'status':['ACTIVE']}, {'status':'ACTIVE','raw':'secret'}):
            assert client.post('/search/jobs/900001:1/state', json=payload, headers=headers).status_code == 422
        assert client.post('/search/jobs/900001:1/state', content='{', headers=headers).status_code == 422
        assert post(client, app, job='unknown').status_code == 404
        assert client.get('/search/jobs/900001:1/state').status_code == 405
        with app.state.search_runs.lock:
            app.state.search_runs.state['status'] = 'running'
        assert post(client, app).status_code == 409
    with Database(pool, read_only=True) as db:
        assert SearchStore(db, migrate=False).user_states() == {}


@pytest.mark.parametrize('status', ['APPLIED', 'EXCLUDED'])
def test_persistence_resave_restart_and_independence(pool, status):
    app = app_for(pool)
    with TestClient(app) as client:
        response = post(client, app, status=status)
        assert response.json() == {'status':status}
        assert response.headers['cache-control'] == 'no-store'
        assert all(value not in response.text for value in ('token', '虚构职责', 'https://'))
        other = 'EXCLUDED' if status == 'APPLIED' else 'APPLIED'
        assert post(client, app, status=other).status_code == 422
    with Database(pool) as db:
        store = SearchStore(db)
        job = store.existing_job('900001:1')
        store.save_job(job)
        store.save_result(job, POLICY_VERSION, SearchEvaluation(verdict='DROP', summary='虚构'), 'mock')
        SearchStore(db)
        assert store.user_states()['900001:1'] == status
        cards = search_cards(store.current_results(), store.user_states())
        assert cards[0]['verdict'] == 'TARGET' and cards[0]['user_status'] == status
    restarted = app_for(pool)
    with TestClient(restarted) as client:
        assert 'Platform 1' not in client.get('/search').text
        assert 'Platform 1' in client.get('/search', params={'status':status}).text
        assert post(client, restarted, status='ACTIVE').status_code == 200
        assert 'Platform 1' in client.get('/search').text


def test_filters_counts_details_preview_and_safe_urls(pool):
    app = app_for(pool)
    with TestClient(app) as client:
        assert post(client, app, status='APPLIED').status_code == 200
        assert post(client, app, job='900001:2', status='EXCLUDED').status_code == 200
        for status, titles in [('ACTIVE',[3]), ('APPLIED',[1]), ('EXCLUDED',[2]), ('ALL',[1,2,3])]:
            page = client.get('/search', params={'status': ['ACTIVE','APPLIED','EXCLUDED'] if status == 'ALL' else [status], 'verdict':['TARGET','POSSIBLE','DROP']}).text
            for index in range(1,4):
                assert (f'Platform {index}' in page) == (index in titles)
            for label in ('待处理（1）', '已投递（1）', '已排除（1）'):
                assert label in page
            assert '虚构地点' * 150 not in page
            assert 'token=fictional' not in page
            for field in ('虚构摘要','虚构理由','虚构风险','虚构职责','虚构要求','虚构加分','Terraform','虚构远程'):
                assert field in page
            compact = page.split('<div class="search-compact">',1)[1].split('<div class="search-details"',1)[0]
            assert 'data-status' not in compact
            assert ' hidden>' in page
        all_page = client.get('/search?status=ACTIVE&status=APPLIED&status=EXCLUDED&verdict=TARGET&verdict=POSSIBLE&verdict=DROP').text
        assert all_page.index('Platform 1') < all_page.index('Platform 2') < all_page.index('Platform 3')
        assert client.get('/search?status=INVALID').status_code == 422
    with Database(pool, read_only=True) as db:
        card = search_cards(SearchStore(db, migrate=False).current_results())[0]
        assert len(card['location_preview']) <= 72
        assert card['location_preview'].endswith('…')
        assert card['url'] == 'https://www.green-japan.com/company/900001/job/1'


def test_confirmation_and_responsive_contract():
    root = Path(__file__).parents[1] / 'scout_agent/web'
    template = (root / 'templates/search.html').read_text()
    first_click = template.split("action.addEventListener('click', () => {",1)[1].split("actions.querySelector('.cancel-state')",1)[0]
    assert 'fetch(' not in first_click
    assert 'confirmation.hidden = false' in first_click
    confirm = template.split("actions.querySelector('.confirm-state').addEventListener",1)[1].split('async function poll()',1)[0]
    assert "method:'POST'" in confirm and "if (!pending) return" in confirm
    assert '之后可在“已排除”中恢复到待处理' in template
    assert "setActionsDisabled(run.status === 'running')" in template
    css = (root / 'static/styles.css').read_text()
    assert '.search-list { display: flex; flex-direction: column;' in css
    assert '-webkit-line-clamp: 2' in css
    assert '@media (max-width: 900px)' in css
    assert '.search-compact { grid-template-columns: minmax(0, 1fr); }' in css


def test_confirmation_clicks_with_fake_dom():
    """Execute the actual page handlers, without a browser or network."""
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('node unavailable for fictional DOM test')
    template = (Path(__file__).parents[1] / 'scout_agent/web/templates/search.html').read_text()
    handlers = template.split('function setActionsDisabled', 1)[1].split('async function poll()', 1)[0]
    script = """
const assert = require('node:assert/strict');
class Element {
  constructor() { this.handlers = {}; this.hidden = true; this.disabled = false; this.dataset = {}; }
  addEventListener(event, fn) { this.handlers[event] = fn; }
  async click() { if (!this.disabled) await this.handlers.click(); }
}
const applied = new Element(), excluded = new Element(), confirm = new Element(), cancel = new Element();
const box = new Element(), message = new Element(), error = new Element(), actions = new Element();
applied.dataset.status = 'APPLIED'; excluded.dataset.status = 'EXCLUDED'; actions.dataset.jobId = '900001:1';
actions.querySelectorAll = () => [applied, excluded];
actions.querySelector = selector => ({
  '.state-confirm':box, '.confirm-message':message, '.confirm-state':confirm,
  '.cancel-state':cancel, '.state-error':error
})[selector];
global.document = {querySelectorAll: selector => ({
  '[data-status], .confirm-state':[applied, excluded, confirm],
  '.expand-job':[], '.user-actions':[actions]
})[selector]};
let calls = [], reloads = 0;
global.fetch = async (...args) => { calls.push(args); return {ok:true}; };
global.location = {reload: () => { reloads++; }};
const csrf = 'fictional';
""" + 'function setActionsDisabled' + handlers + """
(async () => {
  await excluded.click();
  assert.equal(calls.length, 0);
  assert.equal(box.hidden, false);
  assert.match(message.textContent, /已排除/);
  await cancel.click();
  await confirm.click();
  assert.equal(calls.length, 0);
  await applied.click();
  assert.equal(calls.length, 0);
  await confirm.click();
  assert.equal(calls.length, 1);
  assert.equal(JSON.parse(calls[0][1].body).status, 'APPLIED');
  assert.equal(calls[0][1].headers['X-CSRF-Token'], 'fictional');
  assert.equal(reloads, 1);
  setActionsDisabled(true);
  await excluded.click();
  await confirm.click();
  assert.equal(calls.length, 1);
  console.log('fictional DOM: cancel=0 POST; confirm=1 POST; running disables actions');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'confirm=1 POST' in result.stdout
