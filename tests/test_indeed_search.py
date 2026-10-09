"""Fictional offline production checks: no network, browser or paid models."""
import json
import subprocess
from unittest.mock import Mock

import pytest
from js_test_utils import require_node
from test_indeed_discovery import snapshot
from scout_agent.indeed_search import IndeedSearchAdapter, SOURCES, ORIGIN, DETAIL, job_from_url
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search_platforms import FIELDS

ID = 'fictional_ID123'
JD = ORIGIN + '/viewjob?jk=' + ID


def adapter(changes=None, landing=None, fields=None):
    a = IndeedSearchAdapter()
    a.page = Mock(url=landing or JD)
    if landing is None:
        a.page.goto.side_effect = lambda url, **kw: setattr(a.page, 'url', url)
    values = dict.fromkeys(FIELDS, '')
    values.update(title='Fictional SRE', responsibilities='架空基础设施运维')
    a.page.evaluate.side_effect = lambda script: (values if fields is None else fields) if script == DETAIL else snapshot(canonical=JD, links=[JD], **(changes or {}))
    return a


def test_sources_and_mixed_anchor_dedupe():
    a = adapter()
    a.page.evaluate.return_value = None
    links = [ORIGIN + path + '?jk=' + ID + '&private=fictional' for path in ['/rc/clk', '/unknown', '/viewjob']]
    links += ['https://foreign.test/viewjob?jk='+ID, JD+'&jk='+ID, ORIGIN+'/viewjob?jk=short']
    a.page.evaluate.side_effect = lambda _: snapshot(links=links)
    jobs = a.search_cards(SOURCES[0], 1)
    assert [(j.job_id, j.url, j.external_job_id, j.fields) for j in jobs] == [('indeed:'+ID, JD, ID, {})]
    assert a.source_url('SRE') == ORIGIN+'/jobs?q=SRE'
    assert not a.supports_pagination and a.max_verified_page == 1
    for label, page in [('AWS', 1), ('SRE', 2), ('SRE', True)]:
        with pytest.raises(ValueError): a.source_url(label, page)


@pytest.mark.parametrize('suffix', ['?q=SRE&from=fictional', '?q=SRE&start=0&x=fictional'])
def test_source_tracking(suffix):
    assert adapter(landing=ORIGIN+'/jobs'+suffix).search_cards('SRE', 1)


@pytest.mark.parametrize('url', [ORIGIN+'/jobs?q=other', ORIGIN+'/jobs?q=SRE&q=SRE', ORIGIN+'/jobs', ORIGIN+'/jobs/?q=SRE', ORIGIN+'/jobs?q=SRE&start=10', ORIGIN+'/jobs?q=SRE&start=', ORIGIN+'/jobs?q=SRE&start=0&start=0', 'https://foreign.test/jobs?q=SRE'])
def test_source_drift(url):
    a = adapter(landing=url)
    with pytest.raises(GreenSearchDOMPending): a.search_cards('SRE', 1)
    a.page.evaluate.assert_not_called()


@pytest.mark.parametrize('url', [JD+'&from=x', JD+'#', JD+'&jk='+ID, JD.replace('/viewjob', '/rc/clk'), '/viewjob?jk='+ID, JD.replace('https:', 'http:')])
def test_persisted_strict(url):
    with pytest.raises(ValueError): job_from_url(url, {})


def test_detail_tracking_and_identity():
    a = adapter(landing=JD+'&q=fictional&from=fictional')
    assert a.job_detail(job_from_url(JD, {}))['title'] == 'Fictional SRE'
    job = job_from_url(JD, {})
    job.external_job_id = 'fictional_other'
    with pytest.raises(ValueError): a.validate_job(job)


@pytest.mark.parametrize('canonical', [None, 'https://foreign.test/viewjob?jk='+ID, JD+'&jk='+ID, JD.replace(ID, 'fictional_other'), ORIGIN+'/viewjob?jk=short'])
def test_canonical_fail(canonical):
    a = adapter()
    a.page.evaluate.side_effect = lambda _: snapshot(canonical=canonical)
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == 'JOB_URL_MISMATCH'


@pytest.mark.parametrize('changes,reason', [({'challenge': True}, 'CHALLENGE'), ({'login': True}, 'NEEDS_LOGIN'), ({'busy': True}, 'PARSE_ERROR'), ({'ready': 'loading'}, 'PARSE_ERROR')])
@pytest.mark.parametrize('stage', ['list', 'detail'])
def test_state_fail_closed_zero_classifier(changes, reason, stage, tmp_path):
    from scout_agent.search import SearchStore, run_search
    from scout_agent.storage.db import Database
    a = adapter(changes)
    if stage == 'detail':
        a.search_cards = Mock(return_value=[job_from_url(JD, {})])
    model = Mock()
    with Database(tmp_path/'fictional.db') as db:
        with pytest.raises(GreenSearchDOMPending) as exc:
            run_search(a, SearchStore(db), model, keywords=['SRE'])
    assert exc.value.reason == reason
    model.classify_jobs.assert_not_called()
    a.page.wait_for_timeout.assert_not_called()


@pytest.mark.parametrize('drift', ['current', 'canonical'])
def test_post_read_drift(drift):
    a = adapter()
    original = a.page.evaluate.side_effect
    def read(script):
        result = original(script)
        if script == DETAIL:
            if drift == 'current': a.page.url = JD.replace(ID, 'fictional_other')
            else: a.page.evaluate.side_effect = lambda _: snapshot(canonical=JD.replace(ID, 'fictional_other'))
        return result
    a.page.evaluate.side_effect = read
    with pytest.raises(GreenSearchDOMPending) as exc: a.job_detail(job_from_url(JD, {}))
    assert exc.value.reason == 'JOB_URL_MISMATCH'


@pytest.mark.parametrize('field,reason', [('title', 'TITLE_MISSING'), ('responsibilities', 'RESPONSIBILITIES_MISSING')])
def test_missing(field, reason):
    fields = dict.fromkeys(FIELDS, '')
    fields.update(title='Fictional', responsibilities='架空职责'); fields[field] = ''
    with pytest.raises(GreenSearchDOMPending) as exc: adapter(fields=fields).job_detail(job_from_url(JD, {}))
    assert exc.value.reason == reason


def test_cli_web_telemetry(monkeypatch, capsys, tmp_path):
    import scout_agent.cli as cli
    import scout_agent.search as search
    from scout_agent.web.search_runs import SearchRunManager, validate_config
    from scout_agent.web.search_pool import PLATFORMS, source_label
    from scout_agent.web.app import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    def stop(args, settings, *, adapter, progress):
        assert isinstance(adapter, IndeedSearchAdapter)
        progress('SRE', 1)
        raise GreenSearchDOMPending('SOURCE_URL', 'CHALLENGE')
    monkeypatch.setattr(search, '_search_command', stop)
    assert cli.main(['search', 'indeed', '--keyword', 'SRE']) == 1
    line = capsys.readouterr().err.strip()
    assert line == 'Indeed safety stop: source=SRE page=1 category=CHALLENGE'
    manager = SearchRunManager(); manager._emit(line)
    assert manager.snapshot()['safe_reason'] == 'CHALLENGE'
    before = manager.snapshot(); manager._emit('Indeed safety stop: source=private page=1 category=CHALLENGE')
    assert manager.snapshot() == before
    config = validate_config({'platform': 'indeed', 'coverage_pages': 999, 'max_depth': 999})
    assert config['sources'] == list(SOURCES) and config['coverage_pages'] == 2
    assert 'indeed' in PLATFORMS and source_label('indeed', SOURCES[0]) == '基础设施工程师'
    with TestClient(create_app(tmp_path/'fictional.db', search_runner=lambda *args: 0)) as client:
        html = client.get('/search').text
        assert '<option value="indeed">Indeed</option>' in html
        assert '当前固定扫描已验证关键词第一页，分页 offset 待验证' in html
        assert "value === 'indeed'" in html


def test_fixed_jsonld_and_dom_fallback():
    node = require_node()
    script = r'''
const assert = require('assert');
let payload, dom = {};
global.document = {querySelectorAll: () => [{textContent: payload}], querySelector: s => dom[s] || null};
global.getComputedStyle = () => ({visibility: 'visible'});
global.DOMParser = class {parseFromString(html) { return {body: {textContent: html.replace(/<[^>]*>/g, '')}, querySelectorAll: () => []}; }};
const read = DETAIL;
payload = JSON.stringify({'@graph': [{'@type':'JobPosting', title:'Fictional SRE', description:'<p>fictional infra</p>', hiringOrganization:{name:'Fictional Co', secret:'private'}, baseSalary:{currency:'JPY', value:{minValue:600, maxValue:900, unitText:'YEAR', secret:'private'}}, jobLocation:[{address:{addressCountry:{name:'JP'},addressRegion:'Fictional region',addressLocality:'Fictional city',secret:'private'}}]}]});
let v = read(); assert.equal(v.title, 'Fictional SRE'); assert.equal(v.responsibilities, 'fictional infra'); assert.equal(v.salary, 'JPY 600–900 YEAR'); assert.equal(v.location, 'JP Fictional region Fictional city'); assert(!JSON.stringify(v).includes('private'));
payload = JSON.stringify({'@type':'JobPosting', baseSalary:{unknown:'private'},jobLocation:{address:'private'}});
v = read(); assert.equal(v.salary,''); assert.equal(v.location,'');
dom = Object.fromEntries(['h1.jobsearch-JobInfoHeader-title','#jobDescriptionText','[data-testid="inlineHeader-companyName"]'].map(s => [s,{getClientRects:()=>[1],innerText:'fictional fallback'}]));
for (const text of ['malformed', '{}']) { payload=text; v=read(); assert.equal(v.title,'fictional fallback'); assert.equal(v.company,'fictional fallback'); assert.equal(v.responsibilities,'fictional fallback'); }
console.log('fictional JSON-LD and fixed DOM checks passed');
'''.replace('DETAIL', DETAIL)
    result = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_namespace_cache_user_state_and_validated_cards(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search, POLICY_VERSION
    from scout_agent.storage.db import Database
    from scout_agent.web.viewmodels import search_cards
    a = adapter()
    a.search_cards = Mock(return_value=[job_from_url(JD, {})])
    a.job_detail = Mock(return_value={'title':'Fictional SRE', 'responsibilities':'架空基础设施运维'})
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'indeed:'+ID: SearchEvaluation(verdict='POSSIBLE', summary='待确认')}
    with Database(tmp_path/'fictional.db') as db:
        store = SearchStore(db)
        store.advance_source('green:fictional', 7)
        for _ in range(2):
            results, stats = run_search(a, store, model, keywords=SOURCES, coverage_pages=3)
        store.set_user_state('indeed:'+ID, 'EXCLUDED')
        run_search(a, store, model, keywords=['SRE'])
        assert store.user_states()['indeed:'+ID] == 'EXCLUDED'
        assert stats['cache_hits'] == 1 and model.classify_jobs.call_count == 1
        assert stats['source_pages'] == {s:[1] for s in SOURCES}
        assert store.source_cursor('indeed:SRE', 15) == 2
        assert store.source_cursor('green:fictional', 15) == 7
    assert POLICY_VERSION == 'green-search-0.1.1'
    job, result = results[0]
    cards = search_cards([(job,result)], {})
    assert cards[0]['platform_label'] == 'Indeed'
    job.url += '&tracking=fictional'
    assert search_cards([(job,result)], {})[0]['url'] is None
