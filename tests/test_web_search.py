"""Fictional runners only; no browser, recruitment site or model calls."""
import os
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from scout_agent.web.app import create_app
from scout_agent.web.search_runs import SearchRunManager, cli_runner, validate_config


def wait(manager):
    for _ in range(200):
        if manager.snapshot()['status'] != 'running':
            return manager.snapshot()
        time.sleep(.005)
    pytest.fail('fake runner did not finish')


def test_get_csrf_validation_and_single_run(tmp_path):
    release = threading.Event()
    calls = []
    def runner(argv, env, emit):
        calls.append((argv, env))
        emit('Search progress: AWS page 1')
        release.wait(2)
        emit('NEW 职位: 0')
        emit('KNOWN 职位: 40')
        emit('AWS pages: 1,2')
        emit('AWS cursor: 2 → 3')
        emit('PRIVATE JD token=secret https://example.invalid/?cookie=secret')
        return 0
    app = create_app(tmp_path / 'fictional.db', search_runner=runner)
    manager = app.state.search_runs
    with TestClient(app) as client:
        assert client.get('/search').status_code == 200
        assert not calls and not app.state.db_path.exists()
        assert client.get('/search/run').status_code == 405
        for token in ['', 'wrong']:
            response = client.post('/search/run', json={'sources':['AWS']}, headers={'X-CSRF-Token':token})
            assert response.status_code == 403
            assert response.headers['cache-control'] == 'no-store'
        headers = {'X-CSRF-Token':manager.csrf_token}
        assert client.post('/search/run', json={'sources':['AWS'], 'coverage_pages':1}, headers=headers).status_code == 202
        assert client.get('/search/run/status').json()['status'] == 'running'
        assert client.post('/search/run', json={'sources':['AWS']}, headers=headers).status_code == 409
        release.set()
        result = wait(manager)
        assert result['status'] == 'succeeded' and result['exit_code'] == 0
        assert result['finished_at'] and result['config']['sources'] == ['AWS']
        response = client.get('/search/run/status')
        assert 'PRIVATE' not in response.text and 'secret' not in response.text
        assert response.json()['stats']['KNOWN 职位'] == 40
        assert 'AWS cursor: 2 → 3' in response.json()['summary']
        assert response.json()['stats']['source_pages'] == {'AWS': [1, 2]}
        assert response.json()['stats']['cursors'] == {'AWS': {'before': 2, 'after': 3}}
    argv, env = calls[0]
    assert argv[1:] == ['-u','-m','scout_agent','search','green','--keyword','AWS',
        '--coverage-pages','1','--max-depth','15','--max-jobs','30','--max-model-jobs','20']
    assert env['CODEX_BATCH_SIZE'] == '2'


@pytest.mark.parametrize('data', [{'sources':['unknown']}, {'sources':[]}, {'sources':'AWS'},
    {'sources':['AWS','AWS']}, {'command':'evil'}, {'max_depth':1}, {'coverage_pages':0},
    {'max_jobs':501}, {'max_model_jobs':201}, {'codex_batch_size':21},
    {'coverage_pages':True}, {'max_jobs':'30'}, {'max_jobs':1.5}, []])
def test_invalid_request(tmp_path, data):
    app = create_app(tmp_path / 'fictional.db', search_runner=lambda *args: pytest.fail('called'))
    with TestClient(app) as client:
        assert client.post('/search/run', json=data, headers={'X-CSRF-Token':app.state.search_runs.csrf_token}).status_code == 422


@pytest.mark.parametrize('category', ['authentication','quota','timeout','invalid_json','cli_execution_error','browser_unavailable','green_safety_stop'])
def test_failure_safe_category_and_retry(category, monkeypatch):
    monkeypatch.setenv('CODEX_BATCH_SIZE', '9')
    def runner(argv, env, emit):
        assert env['CODEX_BATCH_SIZE'] == '2'
        emit('Traceback PRIVATE JD secret')
        emit('Codex category: ' + category)
        return 1
    manager = SearchRunManager(runner)
    assert manager.start({'sources':['AWS']})
    result = wait(manager)
    assert result['status'] == 'failed' and result['error'] == category
    assert result['summary'] == [] and os.environ['CODEX_BATCH_SIZE'] == '9'
    assert manager.start({'sources':['AWS']})
    wait(manager)


def test_exception_releases_busy():
    def runner(*args):
        raise RuntimeError('PRIVATE credential')
    manager = SearchRunManager(runner)
    manager.start({})
    result = wait(manager)
    assert result['status'] == 'failed' and result['error'] == 'cli_execution_error'
    assert 'PRIVATE' not in str(result)
    assert manager.start({})
    wait(manager)


def test_cli_runner_no_shell(monkeypatch):
    from scout_agent.web import search_runs
    popen = MagicMock()
    process = popen.return_value.__enter__.return_value
    process.stdout.readline.side_effect = ['NEW 职位: 0\n', '']
    process.wait.return_value = 0
    monkeypatch.setattr(search_runs.subprocess, 'Popen', popen)
    lines = []
    assert cli_runner(['fictional', 'argument'], {'CODEX_BATCH_SIZE':'2'}, lines.append) == 0
    assert popen.call_args.args[0] == ['fictional', 'argument']
    assert popen.call_args.kwargs['shell'] is False
    assert lines == ['NEW 职位: 0']


def test_pool_url_strip_query():
    from scout_agent.green_discovery import Job
    from scout_agent.search import SearchEvaluation
    from scout_agent.web.viewmodels import search_cards
    cards = search_cards([(Job('900001:1', 'https://www.green-japan.com/company/900001/job/1?token=secret', {}, ['AWS']),
                           SearchEvaluation(verdict='TARGET', summary='虚构岗位。'))])
    assert cards[0]['url'] == 'https://www.green-japan.com/company/900001/job/1'


def test_safety_stop_context_redaction_and_reset(tmp_path):
    def runner(argv, env, emit):
        emit('Search progress: インフラエンジニア page 1')
        emit('PRIVATE JD https://fictional.invalid/?token=secret')
        emit('Green safety stop: source=インフラエンジニア page=1 category=SOURCE_URL_MISMATCH')
        emit('Green safety stop: source=AWS page=2 category=secret')
        emit('Green safety stop: source=https://fictional.invalid/?token=secret page=1 category=PARSE_ERROR')
        return 1
    app = create_app(tmp_path / 'fictional.db', search_runner=runner)
    manager = app.state.search_runs
    manager.start({'sources':['インフラエンジニア']})
    wait(manager)
    with TestClient(app) as client:
        response = client.get('/search/run/status')
        result = response.json()
        assert result['status'] == 'failed'
        assert result['error'] == 'green_safety_stop'
        assert result['failed_source'] == 'インフラエンジニア'
        assert result['failed_page'] == 1
        assert result['safe_reason'] == 'SOURCE_URL_MISMATCH'
        assert result['summary'][-1] == 'Search progress: インフラエンジニア page 1'
        assert all(word not in response.text for word in ['PRIVATE', 'secret', 'https://', 'token='])
        assert 'run.failed_source' in client.get('/search').text
    manager.runner = lambda *args: 0
    manager.start({'sources':['AWS']})
    assert wait(manager)['safe_reason'] is None
