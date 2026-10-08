"""Fictional Search fixtures; no external access."""
from unittest.mock import Mock
import pytest

from js_test_utils import require_node
from scout_agent.mynavi_search import MynaviSearchAdapter, SOURCE, SOURCES, DETAIL, job_from_url
from scout_agent.platform_discovery import TYPE_SNAPSHOT
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search_platforms import FIELDS
JD = 'https://tenshoku.mynavi.jp/jobinfo-123-456-789-1011/'

def state(**changes):
    data = dict(ready='complete', busy=False, canonical=JD, links=[JD])
    data.update(changes)
    return data

def adapter(data=None, landing=None, fields=None):
    a = MynaviSearchAdapter()
    a.page = Mock(url=landing or JD)
    if landing is None:
        a.page.goto.side_effect = lambda url, **kw: setattr(a.page, 'url', url)
    values = dict.fromkeys(FIELDS, '')
    values.update(company='Fictional', title='Fictional Backend', responsibilities='架空開発', required='架空資格')
    if fields is not None: values = fields
    a.page.evaluate.side_effect = lambda script: values if script == DETAIL else (state() if data is None else data)
    return a

def test_source_builder_tracking_dedup():
    a = adapter(state(links=[JD+'?ty=fictional#test', JD, JD+'#x', 'https://foreign.test/']))
    assert a.source_url(SOURCES[0]) == SOURCE
    assert a.source_url(SOURCES[0], 2) == SOURCE+'pg2/'
    jobs = a.search_cards(SOURCES[0], 2)
    assert [(j.job_id, j.url, j.external_job_id) for j in jobs] == [('mynavi:123-456-789-1011', JD, '123-456-789-1011')]
    for label, page in [('AWS', 1), (SOURCES[0], True), (SOURCES[0], 0)]:
        with pytest.raises(ValueError): a.source_url(label, page)

@pytest.mark.parametrize('landing', [SOURCE+'?', SOURCE+'#', SOURCE+'pg2/', SOURCE.replace('o166', 'o999'), SOURCE.replace('engineer/list/o166', 'fw'), 'https://foreign.test/'])
def test_source_drift(landing):
    a = adapter(landing=landing)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards(SOURCES[0], 1)
    assert exc.value.reason == 'SOURCE_URL_MISMATCH'
    a.page.evaluate.assert_not_called()

@pytest.mark.parametrize('url', [JD+'?', JD+'#', JD+'?ty=x', JD+'extra/', JD.replace('https:', 'http:'), JD.replace('tenshoku.mynavi.jp', 'foreign.test'), '/jobinfo-123-456-789-1011/'])
def test_stored_validator(url):
    with pytest.raises(ValueError): job_from_url(url, {})

@pytest.mark.parametrize('canonical', [JD, JD+'?ty=x#fragment'])
def test_current_tracking(canonical):
    a = adapter(state(canonical=canonical), JD+'?ty=x#fragment')
    assert a.job_detail(job_from_url(JD, {}))['required'] == '架空資格'

@pytest.mark.parametrize('changes,reason', [({'canonical': None}, 'JOB_URL_MISMATCH'), ({'canonical': JD.replace('1011', '1012')}, 'JOB_URL_MISMATCH'), ({'canonical': 'https://foreign.test/'}, 'JOB_URL_MISMATCH'), ({'login': True}, 'NEEDS_LOGIN'), ({'ready': 'loading'}, 'PARSE_ERROR'), ({'busy': True}, 'PARSE_ERROR')])
def test_fail_closed(changes, reason):
    a = adapter(state(**changes))
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == reason

@pytest.mark.parametrize('field,reason', [('title','TITLE_MISSING'), ('responsibilities','RESPONSIBILITIES_MISSING')])
def test_missing_fields(field, reason):
    fields = dict.fromkeys(FIELDS, '')
    fields.update(title='Fictional', responsibilities='架空開発')
    fields[field] = ''
    a = adapter(fields=fields)
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == reason
    assert a.page.wait_for_timeout.call_count == 4

@pytest.mark.parametrize('fields', [None, [], {'title':'fictional'}, dict.fromkeys(FIELDS, 1)])
def test_malformed(fields):
    a = adapter()
    original = a.page.evaluate.side_effect
    a.page.evaluate.side_effect = lambda script: fields if script == DETAIL else original(script)
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == 'PARSE_ERROR'

@pytest.mark.parametrize('drift', ['current', 'canonical'])
def test_post_read_drift(drift):
    a = adapter()
    original = a.page.evaluate.side_effect
    def read(script):
        value = original(script)
        if script == DETAIL:
            if drift == 'current': a.page.url = JD.replace('1011', '1012')
            else: a.page.evaluate.side_effect = lambda script: state(canonical=JD.replace('1011','1012'))
        return value
    a.page.evaluate.side_effect = read
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == 'JOB_URL_MISMATCH'

def test_absence_and_readiness():
    a = adapter(state(links=[]))
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards(SOURCES[0], 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'
    assert a.page.wait_for_timeout.call_count == 4
    a = adapter()
    a.page.evaluate.side_effect = [state(ready='loading'), state()]
    assert a.search_cards(SOURCES[0], 1)
    a.page.wait_for_timeout.assert_called_once_with(500)

def test_cli_web_and_telemetry(monkeypatch, capsys, tmp_path):
    import scout_agent.cli as cli
    import scout_agent.search as search
    from scout_agent.web.search_runs import SearchRunManager, validate_config
    from scout_agent.web.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    def stop(args, settings, *, adapter, progress):
        assert isinstance(adapter, MynaviSearchAdapter)
        progress(args.keyword[0], 2)
        raise GreenSearchDOMPending('JOB_LINKS', 'NO_VALID_JOB_LINKS')
    monkeypatch.setattr(search, '_search_command', stop)
    assert cli.main(['search', 'mynavi', '--keyword', SOURCES[0]]) == 1
    line = capsys.readouterr().err.strip()
    assert line == f'Mynavi safety stop: source={SOURCES[0]} page=2 category=NO_VALID_JOB_LINKS'
    manager = SearchRunManager(); manager._emit(line)
    assert manager.snapshot()['failed_page'] == 2
    before = manager.snapshot()
    manager._emit('Mynavi safety stop: source=AWS page=2 category=NO_VALID_JOB_LINKS')
    manager._emit('Mynavi safe detail diagnostic: private fictional')
    assert manager.snapshot() == before
    config = validate_config({'platform': 'mynavi', 'coverage_pages': 3, 'max_depth': 9})
    assert config['sources'] == list(SOURCES) and config['coverage_pages'] == 3
    with pytest.raises(ValueError): validate_config({'platform': 'mynavi', 'sources': ['AWS']})
    with TestClient(create_app(tmp_path/'fictional.db', search_runner=lambda *args: 0)) as client:
        html = client.get('/search').text
        assert '<option value="mynavi">マイナビ転職</option>' in html
        assert "mynavi: ['インフラエンジニア']" in html


def test_cache_cursor_namespace(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search, POLICY_VERSION
    from scout_agent.storage.db import Database
    a = MynaviSearchAdapter()
    a.search_cards = Mock(return_value=[job_from_url(JD, {})])
    a.job_detail = Mock(return_value={'title': 'Fictional Backend', 'responsibilities': '架空開発'})
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'mynavi:123-456-789-1011': SearchEvaluation(verdict='POSSIBLE', summary='待确认')}
    with Database(tmp_path/'fictional.db') as db:
        store = SearchStore(db)
        history = ['AWS', 'forkwell:求人一覧', 'lapras:求人検索', 'findy:おすすめ求人', 'type:DevOps・SRE', 'doda:インフラエンジニア']
        for source in history: store.advance_source(source, 7)
        for _ in range(2):
            _, stats = run_search(a, store, model, keywords=SOURCES, pages_per_keyword=None, coverage_pages=1, max_depth=15)
        assert stats['cache_hits'] == 1 and model.classify_jobs.call_count == 1
        assert store.source_cursor('mynavi:'+SOURCES[0], 15) != 2
        for source in history: assert store.source_cursor(source, 15) == 7
    assert POLICY_VERSION == 'green-search-0.1.1'


def test_badge_link_and_identity():
    from scout_agent.web.viewmodels import search_cards
    from scout_agent.search import SearchEvaluation
    job = job_from_url(JD, {'title': 'Fictional'})
    result = SearchEvaluation(verdict='POSSIBLE', summary='待确认')
    card = search_cards([(job, result)])[0]
    assert card['platform_label'] == 'マイナビ転職'
    assert card['url'] == JD
    job.external_job_id = '123-456-789-1012'
    assert search_cards([(job, result)])[0]['url'] is None


def test_invalid_cli_source_stops_before_browser(monkeypatch, capsys):
    import scout_agent.cli as cli
    from scout_agent.browser.manager import BrowserManager
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    browser = Mock(side_effect=AssertionError('Browser access forbidden'))
    monkeypatch.setattr(BrowserManager, 'open', browser)
    assert cli.main(['search', 'mynavi', '--keyword', 'AWS']) == 1
    assert 'Mynavi safety stop: source=NONE page=0 category=PARSE_ERROR' in capsys.readouterr().err
    browser.assert_not_called()


def test_telemetry_source_pages_and_cursor():
    from scout_agent.web.search_runs import SearchRunManager
    manager = SearchRunManager()
    manager._emit(f'Search progress: {SOURCES[0]} page 2')
    manager._emit(f'{SOURCES[0]} pages: 1,2')
    manager._emit(f'{SOURCES[0]} cursor: 2 → 3')
    stats = manager.snapshot()['stats']
    assert stats['source_pages'][SOURCES[0]] == [1, 2]
    assert stats['cursors'][SOURCES[0]] == dict(before=2, after=3)

def test_fixed_dom_mapping():
    import json
    import subprocess
    script = r"""
const node = text => ({innerText:text, closest:()=>null});
const required = {...node('架空資格'), closest:()=>true};
const description = {querySelectorAll: selector => {
  if (!selector.includes(':not(.jobPointArea__body--large)')) throw Error('requirements boundary');
  return [node('架空概要'), node('架空詳細'), required];
}};
const local = {'h1 .companyName':node('Fictional'), 'h1 .occName':node('Fictional Backend'),
 '.jobPointArea__wrap-jobDescription':description, '.jobPointArea__body--large':required};
const row = (heading, value) => ({querySelector:s=>node(s==='th'?heading:value)});
global.document = {querySelector:s=>{if (!(s in local)) throw Error('non-local read'); return local[s]},
 querySelectorAll:s=>{if(s!=='table.jobOfferTable tr') throw Error('aggregate read');
 return [row('給与','架空給与'),row('勤務地','架空市'),row('給与補足','ignored')];}};
"""
    result = subprocess.run([require_node(), '-e', script+'\nconsole.log(JSON.stringify(('+DETAIL+')()));'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    fields = json.loads(result.stdout)
    assert fields == dict(company='Fictional', title='Fictional Backend', responsibilities='架空概要\n架空詳細', required='架空資格', salary='架空給与', location='架空市', preferred='', technology='', remote='')


def test_optional_requirements_and_local_retry():
    a = adapter()
    fields = dict.fromkeys(FIELDS, '')
    fields.update(title='Fictional Backend', responsibilities='架空開発')
    a.page.evaluate.side_effect = [state(ready='loading'), state(), fields, state()]
    assert a.job_detail(job_from_url(JD, {}))['required'] == ''
    a.page.wait_for_timeout.assert_called_once_with(500)
    a.page.goto.assert_called_once()

@pytest.mark.parametrize('landing', [SOURCE, SOURCE+'pg3/', SOURCE+'pg2/?x=y', SOURCE+'pg2/#x'])
def test_page_two_drift(landing):
    a = adapter(landing=landing)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards(SOURCES[0], 2)
    assert exc.value.reason == 'SOURCE_URL_MISMATCH'
    a.page.evaluate.assert_not_called()
