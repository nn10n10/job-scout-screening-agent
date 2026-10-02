"""Fictional doda Search evidence; no browser or model access."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scout_agent.doda_search import DodaSearchAdapter, SOURCE, SOURCES, job_from_url, route
from scout_agent.green_discovery import GreenSearchDOMPending

PR = 'https://doda.jp/DodaFront/View/JobSearchDetail/j_jid__900001/-tab__pr/'
JD = PR.replace('-tab__pr', '-tab__jd')


def state(responsibilities='架空アプリ開発', **changes):
    recruit = dict(jobContentOutline=responsibilities, jobContentDetail='<p>架空詳細</p>' if responsibilities else '',
                   targetMemberOutline='架空資格', targetMemberDetail='追加資格',
                   developmentEnvironment='Linux', projectCase='架空案件', salary='架空給与',
                   jobState='架空市', access='架空駅', workTime='', holiday='')
    result = dict(ready='complete', busy=False, canonical=PR, links=[JD], payload=json.dumps(
        {'props': {'pageProps': {'job': {'job': {'jid': 900001, 'corporateName': 'Fictional',
         'occupationName': 'Fictional Backend', 'recruit': recruit}}}}}))
    result.update(changes)
    return result


def adapter(data, landing=None):
    a = DodaSearchAdapter()
    a.page = Mock(url=landing or PR)
    a.page.evaluate.return_value = data
    if landing is None:
        a.page.goto.side_effect = lambda url, **kw: setattr(a.page, 'url', url)
    return a


def test_builder_and_list_dedup():
    a = adapter(dict(ready='complete', links=[PR, JD, PR, JD+'?private=x', 'https://foreign.test/']))
    assert a.source_url(SOURCES[0]) == SOURCE
    assert a.source_url(SOURCES[0], 2) == SOURCE+'-page__2/'
    jobs = a.search_cards(SOURCES[0], 2)
    assert [(j.job_id, j.url, j.external_job_id) for j in jobs] == [('doda:900001', JD, '900001')]
    for label, page in [('AWS', 1), (SOURCES[0], True), (SOURCES[0], 0)]:
        with pytest.raises(ValueError): a.source_url(label, page)


@pytest.mark.parametrize('suffix', ['?', '#', '?x=y', '#x', 'extra/', '-other__a/-more__b/'])
def test_exact_links_reject_suffix(suffix):
    with pytest.raises(ValueError): job_from_url(PR+suffix, {})


@pytest.mark.parametrize('landing', [SOURCE+'?', SOURCE+'#', SOURCE+'?x=y', SOURCE+'-page__2/',
                                     'https://foreign.test/', 'https://doda.jp/other/'])
def test_source_drift(landing):
    a = adapter(dict(ready='complete', links=[JD]), landing)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards(SOURCES[0], 1)
    assert exc.value.reason == 'SOURCE_URL_MISMATCH'
    a.page.evaluate.assert_not_called()


def test_fields_recall_and_no_fallback():
    a = adapter(state())
    fields = a.job_detail(job_from_url(PR, {}))
    assert fields == dict(company='Fictional', title='Fictional Backend',
        responsibilities='架空アプリ開発\n架空詳細', required='架空資格\n追加資格',
        technology='Linux\n架空案件', salary='架空給与', location='架空市\n架空駅', remote='', preferred='')
    a.page.goto.assert_called_once()
    a.page.wait_for_timeout.assert_not_called()


def test_one_jd_fallback_with_slashes():
    a = adapter(state(''))
    def read(script):
        if a.page.url == PR: return state('', links=[JD.replace('/-tab', '//-tab')])
        return state(canonical=JD)
    a.page.evaluate.side_effect = read
    assert a.job_detail(job_from_url(PR, {}))['responsibilities']
    assert [call.args[0] for call in a.page.goto.call_args_list] == [PR, JD]


@pytest.mark.parametrize('links', [[], [JD.replace('900001', '900002')], [JD+'?x=y'], [JD.replace('doda.jp', 'evil.test')]])
def test_missing_responsibilities_no_unsafe_fallback(links):
    a = adapter(state('', links=links))
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(PR, {}))
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'
    a.page.goto.assert_called_once()


def test_fallback_at_most_once():
    a = adapter(state(''))
    with pytest.raises(GreenSearchDOMPending): a.job_detail(job_from_url(PR, {}))
    assert a.page.goto.call_count == 2


@pytest.mark.parametrize('canonical', [PR, JD])
def test_extended_same_jid_canonical(canonical):
    a = adapter(state(canonical=canonical), PR+'-fictional__value/')
    assert a.job_detail(job_from_url(PR, {}))['title']


@pytest.mark.parametrize('landing', [PR+'-fictional__value/-more__value/', PR+'extra/',
    PR+'-fictional__value/?x=y', JD+'-fictional__value/', PR.replace('900001', '900002'),
    PR.replace('doda.jp', 'foreign.test')])
def test_current_route_rejected(landing):
    a = adapter(state(), landing)
    with pytest.raises(GreenSearchDOMPending): a.job_detail(job_from_url(PR, {}))
    a.page.evaluate.assert_not_called()


@pytest.mark.parametrize('changes,reason', [({'canonical': PR.replace('900001', '900002')}, 'JOB_URL_MISMATCH'),
    ({'canonical': PR+'-extra__value/'}, 'JOB_URL_MISMATCH'),
    ({'payload': '{'}, 'PARSE_ERROR'), ({'payload': '[]'}, 'PARSE_ERROR'),
    ({'payload': state()['payload'].replace('900001', '900002')}, 'PARSE_ERROR'),
    ({'payload': state()['payload'].replace('"recruit": {', '"recruit": null, "ignored": {')}, 'PARSE_ERROR'),
    ({'login': True}, 'NEEDS_LOGIN'), ({'ready': 'loading'}, 'PARSE_ERROR'),
    ({'busy': True}, 'PARSE_ERROR'), ({'payload': None}, 'PARSE_ERROR')])
def test_fail_closed(changes, reason):
    a = adapter(state(**changes))
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(PR, {}))
    assert exc.value.reason == reason
    a.page.goto.assert_called_once()


def test_post_read_identity_drift():
    a = adapter(state())
    def read(script):
        a.page.url = PR.replace('900001', '900002')
        return state()
    a.page.evaluate.side_effect = read
    with pytest.raises(GreenSearchDOMPending): a.job_detail(job_from_url(PR, {}))


def test_list_readiness_and_absence():
    a = adapter(dict(ready='loading', links=[]))
    a.page.evaluate.side_effect = [dict(ready='loading'), dict(ready='complete', links=[JD])]
    assert a.search_cards(SOURCES[0], 1)
    a.page.wait_for_timeout.assert_called_once_with(500)
    a = adapter(dict(ready='complete', links=[]))
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards(SOURCES[0], 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'
    assert a.page.wait_for_timeout.call_count == 4


def test_cli_web_and_telemetry(monkeypatch, capsys, tmp_path):
    import scout_agent.cli as cli
    import scout_agent.search as search
    from scout_agent.web.search_runs import SearchRunManager, validate_config
    from scout_agent.web.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    def stop(args, settings, *, adapter, progress):
        assert isinstance(adapter, DodaSearchAdapter)
        progress(args.keyword[0], 2)
        raise GreenSearchDOMPending('JOB_LINKS', 'NO_VALID_JOB_LINKS')
    monkeypatch.setattr(search, '_search_command', stop)
    assert cli.main(['search', 'doda', '--keyword', SOURCES[0]]) == 1
    line = capsys.readouterr().err.strip()
    assert line == f'Doda safety stop: source={SOURCES[0]} page=2 category=NO_VALID_JOB_LINKS'
    manager = SearchRunManager(); manager._emit(line)
    assert manager.snapshot()['failed_page'] == 2
    before = manager.snapshot()
    manager._emit('Doda safety stop: source=AWS page=2 category=NO_VALID_JOB_LINKS')
    manager._emit('Doda safe detail diagnostic: private fictional')
    assert manager.snapshot() == before
    config = validate_config({'platform': 'doda', 'coverage_pages': 3, 'max_depth': 9})
    assert config['sources'] == list(SOURCES) and config['coverage_pages'] == 3
    with pytest.raises(ValueError): validate_config({'platform': 'doda', 'sources': ['AWS']})
    with TestClient(create_app(tmp_path/'fictional.db', search_runner=lambda *args: 0)) as client:
        html = client.get('/search').text
        assert '<option value="doda">doda</option>' in html
        assert "doda: ['インフラエンジニア']" in html


def test_cache_cursor_namespace(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search, POLICY_VERSION
    from scout_agent.storage.db import Database
    a = DodaSearchAdapter()
    a.search_cards = Mock(return_value=[job_from_url(JD, {})])
    a.job_detail = Mock(return_value={'title': 'Fictional Backend', 'responsibilities': '架空開発'})
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'doda:900001': SearchEvaluation(verdict='POSSIBLE', summary='待确认')}
    with Database(tmp_path/'fictional.db') as db:
        store = SearchStore(db)
        history = ['AWS', 'forkwell:求人一覧', 'lapras:求人検索', 'findy:おすすめ求人', 'type:DevOps・SRE']
        for source in history: store.advance_source(source, 7)
        for _ in range(2):
            _, stats = run_search(a, store, model, keywords=SOURCES, pages_per_keyword=None, coverage_pages=1, max_depth=15)
        assert stats['cache_hits'] == 1 and model.classify_jobs.call_count == 1
        assert store.source_cursor('doda:'+SOURCES[0], 15) != 2
        for source in history: assert store.source_cursor(source, 15) == 7
    assert POLICY_VERSION == 'green-search-0.1.1'


def test_badge_link_and_identity():
    from scout_agent.web.viewmodels import search_cards
    from scout_agent.search import SearchEvaluation
    job = job_from_url(JD, {'title': 'Fictional'})
    result = SearchEvaluation(verdict='POSSIBLE', summary='待确认')
    card = search_cards([(job, result)])[0]
    assert card['platform_label'] == 'doda'
    assert card['url'] == JD
    job.external_job_id = '900002'
    assert search_cards([(job, result)])[0]['url'] is None


def test_missing_title_and_no_technology_substitution():
    a = adapter(state(payload=state()['payload'].replace('Fictional Backend', '')))
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(PR, {}))
    assert exc.value.reason == 'TITLE_MISSING'
    a = adapter(state('', links=[]))
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(PR, {}))
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'


def test_invalid_cli_source_stops_before_browser(monkeypatch, capsys):
    import scout_agent.cli as cli
    from scout_agent.browser.manager import BrowserManager
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    browser = Mock(side_effect=AssertionError('Browser access forbidden'))
    monkeypatch.setattr(BrowserManager, 'open', browser)
    assert cli.main(['search', 'doda', '--keyword', 'AWS']) == 1
    assert 'Doda safety stop: source=NONE page=0 category=PARSE_ERROR' in capsys.readouterr().err
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
