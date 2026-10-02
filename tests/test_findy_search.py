"""Fictional snapshots only; no browser, site or paid model access."""
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from scout_agent.findy_search import FindySearchAdapter, job_from_url, parse_fields
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search import search_command
from scout_agent.web.search_runs import validate_config, SearchRunManager
from scout_agent.web.viewmodels import search_cards


def adapter(snapshot, url='https://findy-code.io/recommends'):
    a = FindySearchAdapter()
    a.page = Mock(url=url)
    a.page.evaluate.return_value = {'ready': 'complete', 'login': False, **snapshot}
    return a

@pytest.mark.parametrize('snapshot,reason', [({'login': True}, 'NEEDS_LOGIN'),
    ({'busy': True}, 'PARSE_ERROR'), ({'links': []}, 'NO_VALID_JOB_LINKS')])
def test_list_fail_closed(snapshot, reason):
    a = adapter(snapshot)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('おすすめ求人', 1)
    assert exc.value.reason == reason

def test_list_readiness_second_snapshot_succeeds():
    a = adapter({})
    a.page.evaluate.side_effect = [
        {'ready': 'complete', 'links': ['/jobs/fictional-sre']},
        {'ready': 'complete', 'links': ['/companies/900001/jobs/fictional-key']},
    ]
    assert [j.job_id for j in a.search_cards('おすすめ求人', 1)] == ['findy:900001:fictional-key']
    a.page.goto.assert_called_once()
    a.page.wait_for_timeout.assert_called_once_with(500)
    assert a.page.evaluate.call_count == 2
    assert [call[0] for call in a.page.method_calls] == [
        'goto', 'evaluate', 'wait_for_timeout', 'evaluate']

@pytest.mark.parametrize('links', [[], ['/jobs/fictional-sre']])
def test_list_readiness_wait_has_hard_limit(links):
    a = adapter({'links': links})
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.search_cards('おすすめ求人', 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'
    assert a.page.evaluate.call_count == 5
    assert [call.args for call in a.page.wait_for_timeout.call_args_list] == [(500,)] * 4
    a.page.goto.assert_called_once()

@pytest.mark.parametrize('url,reason', [
    ('https://accounts.google.com/signin', 'NEEDS_LOGIN'),
    ('https://evil.test/recommends', 'SOURCE_URL_MISMATCH'),
])
def test_list_readiness_retry_navigation_fails_closed(url, reason):
    a = adapter({'links': []})
    a.page.wait_for_timeout.side_effect = lambda _: setattr(a.page, 'url', url)
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.search_cards('おすすめ求人', 1)
    assert exc.value.reason == reason
    assert a.page.evaluate.call_count == 1
    a.page.wait_for_timeout.assert_called_once_with(500)

@pytest.mark.parametrize('snapshot,reason', [
    (None, 'PARSE_ERROR'),
    ({'ready': 'complete', 'links': None}, 'PARSE_ERROR'),
    ({'ready': 'complete', 'busy': True, 'links': []}, 'PARSE_ERROR'),
    ({'ready': 'loading', 'links': []}, 'PARSE_ERROR'),
    ({'ready': 'complete', 'login': True, 'links': []}, 'NEEDS_LOGIN'),
])
def test_list_readiness_retry_snapshot_fails_closed(snapshot, reason):
    a = adapter({})
    a.page.evaluate.side_effect = [{'ready': 'complete', 'links': []}, snapshot]
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.search_cards('おすすめ求人', 1)
    assert exc.value.reason == reason
    assert a.page.evaluate.call_count == 2
    a.page.wait_for_timeout.assert_called_once_with(500)

def test_login_redirect_before_read_and_safe_telemetry(capsys):
    a = adapter({}, 'https://accounts.google.com/signin?private=fictional')
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('おすすめ求人', 1)
    assert exc.value.reason == 'NEEDS_LOGIN'
    a.page.evaluate.assert_not_called()
    a.ensure_verified = Mock(side_effect=exc.value)
    assert search_command(SimpleNamespace(platform='findy'), None, adapter=a) == 1
    out = capsys.readouterr().err
    assert 'NEEDS_LOGIN' in out and 'private' not in out

def test_detail_fields_and_identity_recheck():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    detail = {'title': 'Fictional SRE', 'sections': {'仕事内容': '基盤の運用を担当',
        '年収': '600〜900万円', '勤務地': '架空市', '開発環境': 'Terraform', '必須要件': 'Linux'}}
    a.page.evaluate.side_effect = [snapshot, detail, snapshot]
    fields = a.job_detail(job)
    assert fields['responsibilities'] == '基盤の運用を担当'
    assert fields['required'] == 'Linux'
    assert fields['salary'] == '600〜900万円'
    a.page.wait_for_timeout.assert_not_called()
    a.page.evaluate.side_effect = [{**snapshot, 'canonical': 'https://findy-code.io/companies/900002/jobs/other-key'}]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'

def test_missing_responsibilities_is_not_invented():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [snapshot] + [{'title': 'Fictional', 'sections': {'開発環境': 'AWS'}}, snapshot] + [snapshot, {'title': 'Fictional', 'sections': {'開発環境': 'AWS'}}, snapshot] * 4
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'
    assert parse_fields({'unknown': 'ignored'}) == {}

def test_single_platform_web_config_and_runner():
    config = validate_config({'platform': 'findy'})
    assert config['sources'] == ['おすすめ求人']
    with pytest.raises(ValueError): validate_config({'platform': 'findy', 'sources': ['AWS']})
    with pytest.raises(ValueError): validate_config({'platform': 'all'})
    calls = []
    manager = SearchRunManager(runner=lambda argv, env, emit: calls.append(argv) or 0)
    manager._run(config)
    assert calls[0][5:7] == ['findy', '--keyword']
    assert manager.snapshot()['exit_code'] == 0

def test_web_badge_and_link():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    result = SimpleNamespace(verdict='POSSIBLE', summary='待确认', reasons=[], concerns=[])
    card = search_cards([(job, result)])[0]
    assert card['platform_label'] == 'Findy' and card['url'] == job.url
    job.job_id = 'findy:900002:other-key'
    assert search_cards([(job, result)])[0]['url'] is None

def test_findy_cache_state_and_green_history_preserved(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search
    from scout_agent.storage.db import Database
    from scout_agent.search_platforms import Job
    fields = {'title': 'Fictional SRE', 'responsibilities': 'Cloud 基盤の設計と運用', 'salary': '600〜900万円'}
    a = FindySearchAdapter()
    a.search_cards = Mock(side_effect=lambda *args: [job_from_url('/companies/900001/jobs/fictional-key', {})])
    a.job_detail = Mock(return_value=fields)
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'findy:900001:fictional-key': SearchEvaluation(verdict='POSSIBLE', summary='虚构职位待确认。')}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        green = Job.from_url('/company/900001/job/123', fields)
        store.save_job(green)
        store.set_user_state(green.job_id, 'EXCLUDED')
        store.advance_source('AWS', 7)
        _, stats = run_search(a, store, model, keywords=['おすすめ求人'], pages_per_keyword=2)
        assert stats['unique_jobs'] == 1 and stats['model_jobs'] == 1
        store.set_user_state('findy:900001:fictional-key', 'APPLIED')
        _, stats = run_search(a, store, model, keywords=['おすすめ求人'], pages_per_keyword=2)
        assert stats['cache_hits'] == 1 and stats['model_jobs'] == 0
        assert a.job_detail.call_count == 2  # Broad cards have no hashable JD; details refresh before cache lookup.
        assert model.classify_jobs.call_count == 1
        assert store.user_states() == {green.job_id: 'EXCLUDED', 'findy:900001:fictional-key': 'APPLIED'}
        assert store.source_cursor('AWS', 15) == 7
        assert store.existing_job(green.job_id).platform == 'green'

def test_canonical_change_during_detail_read_fails_closed():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    a.page.evaluate.side_effect = [
        {'ready': 'complete', 'canonical': job.url},
        {'title': 'Fictional SRE', 'sections': {'仕事内容': '架空基盤の運用'}},
        {'ready': 'complete', 'canonical': 'https://findy-code.io/companies/900002/jobs/other-key'},
    ]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'

@pytest.mark.parametrize('canonical', [
    '/companies/900002/jobs/fictional-key',
    '/companies/900001/jobs/other-key', None,
    'https://evil.test/companies/900001/jobs/fictional-key'])
def test_detail_canonical_must_be_same_numeric_job(canonical):
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({'canonical': canonical}, job.url)
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'

def test_title_required():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [snapshot] + [{'title': '', 'sections': {'仕事内容': '架空基盤の運用'}}, snapshot] + [snapshot, {'title': '', 'sections': {'仕事内容': '架空基盤の運用'}}, snapshot] * 4
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'TITLE_MISSING'

def test_cli_findy_source_without_running_search(monkeypatch):
    import scout_agent.cli as cli
    import scout_agent.search as search
    calls = []
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    monkeypatch.setattr(search, 'search_command', lambda args, settings: calls.append(args) or 0)
    assert cli.main(['search', 'findy', '--keyword', 'おすすめ求人']) == 0
    assert calls[0].platform == 'findy' and calls[0].keyword == ['おすすめ求人']

def semantic_dom(nodes, og='', title=''):
    """Execute the production JS with fictional elements, never a browser."""
    import json
    import shutil
    import subprocess
    from scout_agent.findy_search import DETAIL
    script = """
const fixture = FIXTURE;
function build([tag,text,children=[],hidden=false,directText]) {
 const node = {tag, nodeType:1, textContent:text, innerText:text, hidden,
 getClientRects(){return this.hidden ? [] : [1]},
 matches(s){return s.split(',').includes(this.tag)}};
 node.children = children.map(build);
 node.childNodes = [{nodeType:3, textContent:directText ?? (children.length ? '' : text)}, ...node.children];
 node.children.forEach((n,i)=>{n.parentElement=node;n.nextElementSibling=node.children[i+1]||null});
 node.querySelectorAll=s=>flatten(node.children).filter(n=>n.matches(s));
 return node;
}
const nodes=fixture.nodes.map(build);
nodes.forEach((n,i)=>n.nextElementSibling=nodes[i+1]||null);
const flatten=ns=>ns.flatMap(n=>[n,...flatten(n.children)]);
global.getComputedStyle=()=>({visibility:'visible'});
global.document={title:fixture.title,
 querySelectorAll(s){return s.startsWith('meta') ?
 (fixture.og ? [{getAttribute:()=>fixture.og}] : []) : flatten(nodes).filter(n=>n.matches(s))},
 get body(){throw Error('Forbidden body access')}};
""".replace('FIXTURE', json.dumps(dict(nodes=nodes, og=og, title=title)))
    result = subprocess.run([shutil.which('node'), '-e', script +
        '\nconsole.log(JSON.stringify((' + DETAIL + ')()));'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    return parse_fields(data['sections'], data['title'])

def test_detail_readiness_shell_then_ready():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [
        snapshot, {'title': 'Fictional document shell', 'sections': {}}, snapshot,
        snapshot, {'title': 'Fictional SRE', 'sections': {'仕事内容': '架空基盤運用'}}, snapshot,
    ]
    assert a.job_detail(job)['responsibilities'] == '架空基盤運用'
    a.page.wait_for_timeout.assert_called_once_with(500)
    a.page.goto.assert_called_once()

@pytest.mark.parametrize('url,snapshot,reason', [
    ('https://accounts.google.com/signin', {}, 'NEEDS_LOGIN'),
    ('https://evil.test/companies/900001/jobs/fictional-key', {}, 'JOB_URL_MISMATCH'),
    ('https://findy-code.io/companies/900001/jobs/fictional-key', {'ready': 'complete', 'canonical': '/companies/900002/jobs/other-key'}, 'JOB_URL_MISMATCH'),
    ('https://findy-code.io/companies/900001/jobs/fictional-key', {'ready': 'complete', 'login': True}, 'NEEDS_LOGIN'),
    ('https://findy-code.io/companies/900001/jobs/fictional-key', {'ready': 'complete', 'busy': True}, 'PARSE_ERROR'),
    ('https://findy-code.io/companies/900001/jobs/fictional-key', {'ready': 'loading'}, 'PARSE_ERROR'),
])
def test_detail_readiness_retry_safety(url, snapshot, reason):
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    initial = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [initial, {'title': 'Shell', 'sections': {}}, initial, snapshot]
    a.page.wait_for_timeout.side_effect = lambda _: setattr(a.page, 'url', url)
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job)
    assert exc.value.reason == reason
    a.page.wait_for_timeout.assert_called_once_with(500)
    a.page.goto.assert_called_once()

@pytest.mark.parametrize('detail', [None, {}, {'title': 'Shell', 'sections': None},
                                      {'title': None, 'sections': {}}])
def test_detail_malformed_has_no_readiness_retry(detail):
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    a.page.evaluate.side_effect = [{'ready': 'complete', 'canonical': job.url}, detail]
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job)
    assert exc.value.reason == 'PARSE_ERROR'
    a.page.wait_for_timeout.assert_not_called()
    assert a.page.evaluate.call_count == 2
@pytest.mark.parametrize('url', [
    'https://evil.test/companies/900001/jobs/fictional-key',
    '/companies/900001/jobs/fictional-key?id=fictional',
    '/companies/900001/jobs/fictional-key#fragment',
    '/companies/900001/jobs/fictional-key/extra',
    '/companies/900001/jobs/fictional%2Fkey',
    '/companies/900001/jobs/bad.key', '/companies/900001',
    '/companies/abc/jobs/fictional', '/companies/900001/jobs/',
])
def test_strict_job_identity_rejects(url):
    with pytest.raises(ValueError):
        job_from_url(url, {})


def test_combined_identity_and_source_pages():
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    assert job.job_id == 'findy:900001:fictional-key'
    assert job.external_job_id == '900001:fictional-key'
    assert job_from_url('/companies/900002/jobs/fictional-key', {}).job_id != job.job_id
    a = FindySearchAdapter()
    assert a.source_url('おすすめ求人') == 'https://findy-code.io/recommends'
    assert a.source_url('おすすめ求人', 2) == 'https://findy-code.io/recommends?page=2'
    for label, page in [('AWS', 1), ('おすすめ求人', True), ('おすすめ求人', 0)]:
        with pytest.raises(ValueError):
            a.source_url(label, page)


@pytest.mark.parametrize('url', [
    'https://findy-code.io/recommends?page=1',
    'https://findy-code.io/recommends?page=3',
    'https://evil.test/recommends?page=2',
])
def test_source_page_drift(url):
    a = adapter({}, url)
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.search_cards('おすすめ求人', 2)
    assert exc.value.reason == 'SOURCE_URL_MISMATCH'
    a.page.evaluate.assert_not_called()


def test_semantic_fields_and_boundaries():
    fields = semantic_dom([
        ['h1', 'hidden', [], True], ['h1', ''], ['h1', 'Fictional SRE'],
        ['span', '仕事内容'], ['div', '', [
            ['p', '架空基盤運用'], ['span', '勤務時間'], ['p', 'EXCLUDED']]],
        ['dt', '給与'], ['dd', '600万円'], ['dt', '勤務地'], ['dd', '架空市'],
        ['h2', '開発環境'], ['p', 'Linux'], ['h2', '福利厚生'], ['p', 'EXCLUDED'],
        ['h2', '必須要件'], ['p', 'Terraform'], ['h2', '歓迎スキル'], ['p', 'Python'],
    ])
    assert fields == dict(title='Fictional SRE', responsibilities='架空基盤運用',
                         salary='600万円', location='架空市', technology='Linux',
                         required='Terraform', preferred='Python')
    assert semantic_dom([], og='Fictional | Findy', title='Fictional | Findy') == {}


def test_cursor_namespace_and_known_cache(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search
    from scout_agent.storage.db import Database
    a = FindySearchAdapter()
    a.search_cards = Mock(side_effect=lambda *_: [job_from_url('/companies/900001/jobs/fictional-key', {})])
    a.job_detail = Mock(return_value={'title': 'Fictional SRE', 'responsibilities': 'Cloud 基盤設計運用'})
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'findy:900001:fictional-key': SearchEvaluation(verdict='POSSIBLE', summary='待确认')}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        for source in ['AWS', 'forkwell:求人一覧', 'lapras:求人検索']:
            store.advance_source(source, 7)
        _, stats = run_search(a, store, model, keywords=['おすすめ求人'], pages_per_keyword=None, coverage_pages=1, max_depth=10)
        assert stats['source_pages']['おすすめ求人'] == [1, 2]
        assert store.source_cursor('findy:おすすめ求人', 10) == 3
        _, stats = run_search(a, store, model, keywords=['おすすめ求人'], pages_per_keyword=None, coverage_pages=1, max_depth=10)
        assert stats['cache_hits'] == 1
        assert model.classify_jobs.call_count == 1
        assert a.job_detail.call_count == 1
        for source in ['AWS', 'forkwell:求人一覧', 'lapras:求人検索']:
            assert store.source_cursor(source, 10) == 7


def test_fixed_safe_telemetry():
    manager = SearchRunManager()
    manager._emit('Search progress: おすすめ求人 page 2')
    manager._emit('おすすめ求人 pages: 1,2')
    manager._emit('おすすめ求人 cursor: 2 → 3')
    manager._emit('Findy safety stop: source=おすすめ求人 page=2 category=NO_VALID_JOB_LINKS')
    state = manager.snapshot()
    assert state['safe_reason'] == 'NO_VALID_JOB_LINKS'
    assert state['failed_page'] == 2
    assert state['stats']['source_pages']['おすすめ求人'] == [1, 2]
    assert state['stats']['cursors']['おすすめ求人'] == {'before': 2, 'after': 3}
    before = manager.snapshot()
    manager._emit('Findy safety stop: source=PRIVATE page=2 category=NO_VALID_JOB_LINKS')
    assert manager.snapshot() == before


def test_web_platform_source_and_live_controls(tmp_path):
    from fastapi.testclient import TestClient
    from scout_agent.web.app import create_app
    app = create_app(tmp_path / 'fictional.db', search_runner=lambda *args: 0)
    with TestClient(app) as client:
        html = client.get('/search').text
        assert '<option value="findy">Findy</option>' in html
        assert "findy: ['おすすめ求人']" in html
        assert "const singlePage = form.elements.platform.value === 'lapras'" in html
        assert not (tmp_path / 'fictional.db').exists()
    config = validate_config({'platform': 'findy', 'coverage_pages': 4, 'max_depth': 20})
    assert config['coverage_pages'] == 4 and config['max_depth'] == 20
    with pytest.raises(ValueError):
        validate_config({'platform': 'findy', 'sources': ['求人検索']})


def test_cli_safety_context_is_fixed(monkeypatch, capsys):
    import scout_agent.search as search
    def stop(args, settings, *, adapter, progress):
        progress('おすすめ求人', 2)
        raise GreenSearchDOMPending('JOB_LINKS', 'NO_VALID_JOB_LINKS')
    monkeypatch.setattr(search, '_search_command', stop)
    assert search.search_command(SimpleNamespace(platform='findy'), None) == 1
    assert capsys.readouterr().err.strip() == (
        'Findy safety stop: source=おすすめ求人 page=2 category=NO_VALID_JOB_LINKS')


def test_first_list_success_only_reads_once():
    a = adapter({'links': ['/companies/900001/jobs/fictional-key']})
    assert len(a.search_cards('おすすめ求人', 1)) == 1
    a.page.evaluate.assert_called_once()
    a.page.goto.assert_called_once()
    a.page.wait_for_timeout.assert_not_called()


@pytest.mark.parametrize('boundary', ['給与', '勤務時間', '福利厚生', '雇用形態', 'Other heading'])
def test_fixed_section_boundary(boundary):
    fields = semantic_dom([['h1', 'Fictional'], ['h2', '仕事内容'],
                           ['p', '架空運用'], ['h3', boundary], ['p', 'EXCLUDED']])
    assert fields['responsibilities'] == '架空運用'


def test_inline_text_is_preserved():
    fields = semantic_dom([['h1', 'Fictional'], ['span', '仕事内容'],
                           ['div', '', [['span', '架空改善']], False, '架空運用']])
    assert fields['responsibilities'] == '架空運用\n架空改善'


@pytest.mark.parametrize('label,key', [('仕事内容', 'responsibilities'),
    ('開発環境', 'technology'), ('給与', 'salary'), ('年収', 'salary'),
    ('勤務地', 'location')])
@pytest.mark.parametrize('depth', [1, 2, 3, 4])
def test_ancestor_fallback_allowlist_and_depth(label, key, depth):
    wrapped = ['h2', label]
    for _ in range(depth):
        wrapped = ['div', '', [wrapped]]
    fields = semantic_dom([['h1', 'Fictional'], ['section', '', [
        wrapped, ['div', '', [['p', '架空本文']]]]],
        ['section', '', [['h2', '福利厚生'], ['p', 'EXCLUDED']]]])
    assert fields.get(key) == ('架空本文' if depth <= 3 else None)


@pytest.mark.parametrize('boundary', [['span', '給与'], ['h3', 'Other heading'],
                                      ['section', '', [['p', 'EXCLUDED']]]])
def test_ancestor_fallback_stops_inside_body_subtree(boundary):
    fields = semantic_dom([['h1', 'Fictional'], ['section', '', [
        ['div', '', [['h2', '仕事内容']]], ['div', '', [
            ['p', '架空本文'], boundary, ['p', 'EXCLUDED']]]]]])
    assert fields['responsibilities'] == '架空本文'


def test_direct_sibling_precedes_ancestor_fallback():
    fields = semantic_dom([['h1', 'Fictional'], ['section', '', [
        ['div', '', [['h2', '仕事内容'], ['p', '架空直接本文']]],
        ['div', '', [['p', 'EXCLUDED']]]]]])
    assert fields['responsibilities'] == '架空直接本文'


def test_table_direct_siblings_preserved():
    fields = semantic_dom([['table', '', [
        ['tr', '', [['th', '給与'], ['td', '架空600万円']]],
        ['tr', '', [['th', '勤務地'], ['td', '架空市']]]]]])
    assert fields == {'salary': '架空600万円', 'location': '架空市'}


@pytest.mark.parametrize('sibling', [None, ['div', '', [['p', 'EXCLUDED']], True],
                                   ['div', '', [['h3', 'Other heading'], ['p', 'EXCLUDED']]]])
def test_ancestor_no_local_value_remains_missing(sibling):
    children = [['div', '', [['h2', '仕事内容']]]]
    if sibling:
        children.append(sibling)
    fields = semantic_dom([['h1', 'Fictional'], ['section', '', children]])
    assert 'responsibilities' not in fields
    job = job_from_url('/companies/900001/jobs/fictional-key', {})
    a = adapter({}, job.url)
    a.page.evaluate.return_value = {'ready': 'complete', 'canonical': job.url,
                                  'title': 'Fictional', 'sections': {}}
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job)
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'


def test_ancestor_skips_hidden_and_empty_siblings():
    fields = semantic_dom([['section', '', [
        ['div', '', [
            ['div', '', [['h2', '仕事内容']]],
            ['div', 'EXCLUDED', [], True]]],
        ['div', '', [['h3', 'Other heading']]]]],
        ['div', '', [['p', '架空第三层本文']]]])
    assert fields['responsibilities'] == '架空第三层本文'
