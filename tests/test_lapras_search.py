"""Fictional snapshots only; no browser, site or paid model access."""
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from scout_agent.lapras_search import LaprasSearchAdapter, job_from_url, parse_fields
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search import search_command
from scout_agent.web.search_runs import validate_config, SearchRunManager
from scout_agent.web.viewmodels import search_cards


@pytest.mark.parametrize('url', ['/jobs/123', '/jobs/123?tracking=fictional'])
def test_numeric_identity_and_canonical(url):
    job = job_from_url(url, {})
    assert job.job_id == 'lapras:123'
    assert job.external_job_id == '123'
    assert '?' not in job.url
    assert LaprasSearchAdapter().validate_job(job) == job.url


@pytest.mark.parametrize('url', ['http://jobs.lapras.com/jobs/1', 'https://evil.test/jobs/1',
    'https://lapras.com.evil.test/jobs/1', 'https://user@jobs.lapras.com/jobs/1',
    '/jobs/search', '/jobs/abc', '/jobs/1/apply', '/jobs/1\n', '', None])
def test_job_allowlist(url):
    with pytest.raises(ValueError):
        job_from_url(url, {})


def adapter(snapshot, url='https://lapras.com/jobs/search'):
    a = LaprasSearchAdapter()
    a.page = Mock(url=url)
    a.page.evaluate.return_value = {'ready': 'complete', 'login': False, **snapshot}
    return a


def test_broad_list_routes_and_deduplication():
    a = adapter({'links': ['/jobs/123', '/jobs/123?tracking=fictional', 'https://evil.test/jobs/2']})
    assert [j.job_id for j in a.search_cards('求人検索', 1)] == ['lapras:123']
    a.page.goto.assert_called_once_with('https://lapras.com/jobs/search', wait_until='load', timeout=15000)
    with pytest.raises(ValueError): a.source_url('求人検索', 2)
    for label, page in [('AWS', 1), ('求人検索', 0), ('求人検索', True)]:
        with pytest.raises(ValueError): a.source_url(label, page)


@pytest.mark.parametrize('snapshot,reason', [({'login': True}, 'NEEDS_LOGIN'),
    ({'busy': True}, 'PARSE_ERROR'), ({'links': []}, 'NO_STABLE_JOB_LINKS')])
def test_list_fail_closed(snapshot, reason):
    a = adapter(snapshot)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人検索', 1)
    assert exc.value.reason == reason


def test_login_redirect_before_read_and_safe_telemetry(capsys):
    a = adapter({}, 'https://accounts.google.com/signin?private=fictional')
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人検索', 1)
    assert exc.value.reason == 'NEEDS_LOGIN'
    a.page.evaluate.assert_not_called()
    a.ensure_verified = Mock(side_effect=exc.value)
    assert search_command(SimpleNamespace(platform='lapras'), None, adapter=a) == 1
    out = capsys.readouterr().err
    assert 'NEEDS_LOGIN' in out and 'private' not in out


def test_detail_fields_and_identity_recheck():
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    detail = {'title': 'Fictional SRE', 'sections': {'仕事内容': '基盤の運用を担当',
        '年収': '600〜900万円', '勤務地': '架空市', '開発環境': 'Terraform', '必須要件': 'Linux'}}
    a.page.evaluate.side_effect = [snapshot, detail, snapshot]
    fields = a.job_detail(job)
    assert fields['responsibilities'] == '基盤の運用を担当'
    assert fields['required'] == 'Linux'
    assert fields['salary'] == '600〜900万円'
    a.page.evaluate.side_effect = [{**snapshot, 'canonical': 'https://lapras.com/jobs/999'}]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'


def test_missing_responsibilities_is_not_invented():
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [snapshot, {'title': 'Fictional', 'sections': {'開発環境': 'AWS'}}, snapshot]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'
    assert parse_fields({'unknown': 'ignored'}) == {}


def test_single_platform_web_config_and_runner():
    config = validate_config({'platform': 'lapras'})
    assert config['sources'] == ['求人検索']
    with pytest.raises(ValueError): validate_config({'platform': 'lapras', 'sources': ['AWS']})
    with pytest.raises(ValueError): validate_config({'platform': 'all'})
    calls = []
    manager = SearchRunManager(runner=lambda argv, env, emit: calls.append(argv) or 0)
    manager._run(config)
    assert calls[0][5:7] == ['lapras', '--keyword']
    assert manager.snapshot()['exit_code'] == 0


def test_web_badge_and_link():
    job = job_from_url('/jobs/123', {})
    result = SimpleNamespace(verdict='POSSIBLE', summary='待确认', reasons=[], concerns=[])
    card = search_cards([(job, result)])[0]
    assert card['platform_label'] == 'LAPRAS' and card['url'] == job.url
    job.job_id = 'lapras:999'
    assert search_cards([(job, result)])[0]['url'] is None


def test_lapras_cache_state_and_green_history_preserved(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search
    from scout_agent.storage.db import Database
    from scout_agent.search_platforms import Job
    fields = {'title': 'Fictional SRE', 'responsibilities': 'Cloud 基盤の設計と運用', 'salary': '600〜900万円'}
    a = LaprasSearchAdapter()
    a.search_cards = Mock(side_effect=lambda *args: [job_from_url('/jobs/123', {})])
    a.job_detail = Mock(return_value=fields)
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'lapras:123': SearchEvaluation(verdict='POSSIBLE', summary='虚构职位待确认。')}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        green = Job.from_url('/company/900001/job/123', fields)
        store.save_job(green)
        store.set_user_state(green.job_id, 'EXCLUDED')
        store.advance_source('AWS', 7)
        _, stats = run_search(a, store, model, keywords=['求人検索'], pages_per_keyword=2)
        assert stats['unique_jobs'] == 1 and stats['model_jobs'] == 1
        store.set_user_state('lapras:123', 'APPLIED')
        _, stats = run_search(a, store, model, keywords=['求人検索'], pages_per_keyword=2)
        assert stats['cache_hits'] == 1 and stats['model_jobs'] == 0
        assert a.job_detail.call_count == 2  # Broad cards have no hashable JD; details refresh before cache lookup.
        assert model.classify_jobs.call_count == 1
        assert store.user_states() == {green.job_id: 'EXCLUDED', 'lapras:123': 'APPLIED'}
        assert store.source_cursor('AWS', 15) == 7
        assert store.existing_job(green.job_id).platform == 'green'


@pytest.mark.parametrize('url', ['https://evil.test/jobs', 'https://lapras.com/profile',
    'https://lapras.com/jobs/search?page=99'])
def test_foreign_and_unexpected_redirects_stop_before_dom_read(url):
    a = adapter({}, url)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人検索', 1)
    assert exc.value.reason == 'SOURCE_URL_MISMATCH'
    a.page.evaluate.assert_not_called()


def test_detail_login_redirect_during_read_is_not_parse_error():
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    def redirect(script):
        a.page.url = 'https://accounts.google.com/signin'
        raise ValueError('fictional private diagnostic')
    a.page.evaluate.side_effect = redirect
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'NEEDS_LOGIN'


def test_malformed_snapshot_fails_closed():
    a = adapter({})
    a.page.evaluate.return_value = None
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人検索', 1)
    assert exc.value.reason == 'PARSE_ERROR'



@pytest.mark.parametrize('pages', [None, 8])
def test_single_page_does_not_read_or_advance_cursor(pages):
    from scout_agent.search import run_search
    a = LaprasSearchAdapter()
    a.search_cards = Mock(return_value=[])
    store, model = Mock(), Mock()
    _, stats = run_search(a, store, model, keywords=['求人検索'], pages_per_keyword=pages)
    a.search_cards.assert_called_once_with('求人検索', 1)
    store.source_cursor.assert_not_called()
    store.advance_source.assert_not_called()
    assert stats['source_pages'] == {'求人検索': [1]}
    assert stats['cursors'] == {}


def test_slug_links_never_become_identity():
    a = adapter({'links': ['/jobs/fictional-sre', '/jobs/123', '/jobs/search']})
    assert [j.job_id for j in a.search_cards('求人検索', 1)] == ['lapras:123']
    assert a.ignored_slug_links == 1
    a = adapter({'links': ['/jobs/fictional-sre']})
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人検索', 1)
    assert exc.value.reason == 'NO_STABLE_JOB_LINKS'


def test_actual_semantic_script_and_allowlist():
    import json, subprocess, shutil
    from scout_agent.lapras_search import DETAIL
    prefix = """
const nodes = [['h1','Fictional SRE'],['h2','業務内容'],['p','架空基盤の運用'],
 ['dt','給与'],['dd','600万円'],['dt','勤務地'],['dd','架空市'],
 ['h2','開発環境'],['p','Linux'],['dt','雇用形態'],['dd','正社員'],
 ['h2','unknown'],['p','ignore']].map(([tag,text]) => ({tag,textContent:text,innerText:text,
 getClientRects(){return [1]},matches(s){return s.split(',').includes(this.tag)}}));
nodes.forEach((n,i)=>n.nextElementSibling=nodes[i+1]||null);
global.getComputedStyle=()=>({visibility:'visible'});
global.document={querySelectorAll:s=>nodes.filter(n=>n.matches(s)),get body(){throw Error('Forbidden')}};
"""
    result = subprocess.run([shutil.which('node'), '-e', prefix + '\nconsole.log(JSON.stringify((' + DETAIL + ')()));'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert 'unknown' not in data['sections']
    fields = parse_fields(data['sections'], data['title'])
    assert fields['responsibilities'] == '架空基盤の運用'
    assert fields['employment'] == '正社員'
    assert fields['salary'] == '600万円'
    assert fields['location'] == '架空市'
    assert fields['technology'] == 'Linux'
    assert 'responsibilities' not in parse_fields({'求人概要': 'not duties'}, 'title')


def test_identity_namespaces():
    from scout_agent.forkwell_discovery import job_from_url as fork_job
    from scout_agent.search_platforms import Job, source_identity
    assert len({job_from_url('/jobs/123', {}).job_id, fork_job('/jobs/123', {}).job_id,
                Job.from_url('/company/900001/job/123', {}).job_id}) == 3
    assert source_identity('lapras', '求人検索') == 'lapras:求人検索'


def test_canonical_change_during_detail_read_fails_closed():
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    a.page.evaluate.side_effect = [
        {'ready': 'complete', 'canonical': job.url},
        {'title': 'Fictional SRE', 'sections': {'業務内容': '架空基盤の運用'}},
        {'ready': 'complete', 'canonical': 'https://lapras.com/jobs/999'},
    ]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'


@pytest.mark.parametrize('canonical', ['/jobs/fictional-sre', '/jobs/999', None])
def test_detail_canonical_must_be_same_numeric_job(canonical):
    job = job_from_url('/jobs/123', {})
    a = adapter({'canonical': canonical}, job.url)
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'JOB_URL_MISMATCH'


def test_title_required():
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [snapshot, {'title': '', 'sections': {'仕事内容': '架空基盤の運用'}}, snapshot]
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job)
    assert exc.value.reason == 'TITLE_MISSING'


def test_web_page_and_safe_lapras_telemetry(tmp_path):
    from fastapi.testclient import TestClient
    from scout_agent.web.app import create_app
    app = create_app(tmp_path / 'fictional.db', search_runner=lambda *args: 0)
    with TestClient(app) as client:
        html = client.get('/search').text
        assert '<option value="lapras">LAPRAS</option>' in html
        assert "lapras: ['求人検索']" in html
        assert '暂不支持已验证分页' in html
        assert not (tmp_path / 'fictional.db').exists()  # GET never migrates.
    manager = SearchRunManager(runner=lambda argv, env, emit:
        emit('LAPRAS safety stop: source=求人検索 page=1 category=NO_STABLE_JOB_LINKS') or 1)
    manager._run(validate_config({'platform': 'lapras'}))
    assert manager.snapshot()['safe_reason'] == 'NO_STABLE_JOB_LINKS'
    assert manager.snapshot()['failed_source'] == '求人検索'


def test_cli_lapras_source_without_running_search(monkeypatch):
    import scout_agent.cli as cli
    import scout_agent.search as search
    calls = []
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    monkeypatch.setattr(search, 'search_command', lambda args, settings: calls.append(args) or 0)
    assert cli.main(['search', 'lapras', '--keyword', '求人検索']) == 0
    assert calls[0].platform == 'lapras' and calls[0].keyword == ['求人検索']


def semantic_dom(nodes, og='', title=''):
    """Execute the production JS with fictional elements, never a browser."""
    import json
    import shutil
    import subprocess
    from scout_agent.lapras_search import DETAIL
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


@pytest.mark.parametrize('nodes,og,title,expected', [
    ([], 'Fictional SRE | LAPRAS（ラプラス）', '', 'Fictional SRE'),
    ([], '', 'Fictional Platform | LAPRAS', 'Fictional Platform'),
    ([], '', 'Fictional Platform | LAPRAS（ラプラス）', 'Fictional Platform'),
    ([['h1', ''], ['h1', 'Fictional H1']], 'Other | LAPRAS', '', 'Fictional H1'),
    ([['h1', 'hidden', [], True]], 'Visible | LAPRAS', '', 'Visible'),
    ([], '', '', None),
    ([], 'LAPRAS', 'LAPRAS（ラプラス）', None),
    ([], '', ' | LAPRAS', None),
    ([], '', 'Fictional | Different site', 'Fictional | Different site'),
])
def test_semantic_title_fallback(nodes, og, title, expected):
    assert semantic_dom(nodes, og, title).get('title') == expected


@pytest.mark.parametrize('nodes,expected', [
    ([['span', '仕事内容'], ['p', '架空基盤の運用']], '架空基盤の運用'),
    ([['div', '仕事内容'], ['h3', '概要'], ['p', '架空基盤の運用']], '架空基盤の運用'),
    ([['label', '仕事内容'], ['div', '概要\n架空基盤の運用',
      [['strong', '概要'], ['p', '架空基盤の運用']]]], '架空基盤の運用'),
    ([['strong', '概要'], ['p', '架空基盤の運用']], None),
    ([['span', '仕事内容'], ['span', '給与'], ['p', '600万円']], None),
    ([['span', '仕事内容'], ['p', '架空基盤の運用'], ['div', '給与'],
      ['p', '600万円']], '架空基盤の運用'),
    ([['span', '仕事内容'], ['div', 'mixed', [['p', '架空基盤の運用'],
      ['span', '給与'], ['p', '600万円']]]], '架空基盤の運用'),
    ([['span', '仕事内容の紹介'], ['p', '架空基盤の運用']], None),
    ([['div', '仕事内容'], ['h3', '概要']], None),
])
def test_semantic_local_responsibilities(nodes, expected):
    assert semantic_dom(nodes, title='Fictional | LAPRAS').get('responsibilities') == expected


def ancestor_summary_dom(depth, label='仕事内容', summary='概要', carrier='h4', direct=None,
                         following=None):
    children = [['p', label]]
    if direct:
        children.append(['p', direct])
    wrapper = ['div', '', children]
    for _ in range(depth - 1):
        wrapper = ['div', '', [wrapper]]
    return [wrapper, ['section', '', [['div', '', [
        [carrier, summary], ['div', '', [['p', '架空基盤の運用']]],
        *(following or [])]]]]]


@pytest.mark.parametrize('depth', [1, 2, 3])
@pytest.mark.parametrize('label', ['仕事内容', '業務内容', '職務内容'])
def test_responsibilities_ancestor_summary(depth, label):
    assert semantic_dom(ancestor_summary_dom(depth, label)).get('responsibilities') == '架空基盤の運用'


@pytest.mark.parametrize('options', [
    {'depth': 4}, {'depth': 3, 'summary': '求人概要'},
    {'depth': 3, 'summary': '概要の紹介'}, {'depth': 3, 'carrier': 'p'},
    {'depth': 3, 'label': '応募資格'},
])
def test_responsibilities_ancestor_summary_rejected(options):
    assert 'responsibilities' not in semantic_dom(ancestor_summary_dom(**options))


@pytest.mark.parametrize('boundary', ['給与', '応募資格', 'Other fictional heading'])
def test_responsibilities_ancestor_summary_boundary(boundary):
    nodes = ancestor_summary_dom(3, following=[['h4', boundary], ['p', 'FICTIONAL EXCLUDED']])
    assert semantic_dom(nodes).get('responsibilities') == '架空基盤の運用'


def test_responsibilities_ancestor_summary_preserves_direct():
    assert semantic_dom(ancestor_summary_dom(3, direct='架空の直接業務')).get('responsibilities') == '架空の直接業務'


def test_summary_without_responsibilities_label():
    assert 'responsibilities' not in semantic_dom([['h4', '概要'], ['p', '架空基盤の運用']])


@pytest.mark.parametrize('intro', ['h1', 'h2', 'h3', '[role=heading]'])
def test_summary_nested_intro_once(intro):
    body = ['div', '', [['div', '', [[intro, 'Fictional intro'],
        ['p', '架空設計'], ['p', '架空運用'], ['p', '架空改善']]]]]
    nodes = ancestor_summary_dom(3)
    nodes[1][2][0][2][1] = body
    assert semantic_dom(nodes)['responsibilities'] == '架空設計\n架空運用\n架空改善'


@pytest.mark.parametrize('children,expected', [
    ([['h2', '必須要件'], ['p', 'EXCLUDED']], None),
    ([['h1', 'Intro'], ['h3', 'Second intro'], ['p', 'EXCLUDED']], None),
    ([['h1', 'Intro'], ['p', '架空設計'], ['h6', 'Boundary'], ['p', 'EXCLUDED']], '架空設計'),
    ([['p', '架空設計'], ['p', '架空運用']], '架空設計\n架空運用'),
    ([['ul', '', [['li', '架空設計'], ['li', '架空運用']]]], '架空設計\n架空運用'),
    ([['p', 'HIDDEN', [], True], ['p', '架空設計']], '架空設計'),
    ([['div', '架空設計', [['p', '架空設計']]]], '架空設計'),
    ([['p', '架空設計'], ['span', '給与'], ['p', 'EXCLUDED']], '架空設計'),
    ([['h2', '必須'], ['p', 'EXCLUDED']], None),
    ([['h2', '歓迎'], ['p', 'EXCLUDED']], None),
    ([['p', '']], None),
    ([['p', '架空設計', [['span', '架空運用']], False, '架空設計']], '架空設計\n架空運用'),
])
def test_summary_local_document_order_and_boundaries(children, expected):
    nodes = ancestor_summary_dom(3, following=[['p', 'OUTSIDE EXCLUDED']])
    nodes[1][2][0][2][1] = ['div', '', [['div', '', children]]]
    assert semantic_dom(nodes).get('responsibilities') == expected


def test_summary_intro_cannot_cross_immediate_sibling():
    nodes = ancestor_summary_dom(3, following=[['p', 'OUTSIDE EXCLUDED']])
    nodes[1][2][0][2][1] = ['div', '', [['h1', 'Fictional intro']]]
    assert 'responsibilities' not in semantic_dom(nodes)
