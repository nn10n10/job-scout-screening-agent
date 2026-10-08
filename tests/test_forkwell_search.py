"""Fictional snapshots only; no browser, site or paid model access."""
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from js_test_utils import require_node

from scout_agent.forkwell_discovery import ForkwellSearchAdapter, job_from_url, parse_fields
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search import search_command
from scout_agent.web.search_runs import validate_config, SearchRunManager
from scout_agent.web.viewmodels import search_cards


@pytest.mark.parametrize('url', ['/jobs/123', '/fictional-company/jobs/123/?tracking=fictional'])
def test_numeric_identity_and_canonical(url):
    job = job_from_url(url, {})
    assert job.job_id == 'forkwell:123'
    assert job.external_job_id == '123'
    assert '?' not in job.url
    assert ForkwellSearchAdapter().validate_job(job) == job.url


@pytest.mark.parametrize('url', ['http://jobs.forkwell.com/jobs/1', 'https://evil.test/jobs/1',
    'https://jobs.forkwell.com.evil.test/jobs/1', 'https://user@jobs.forkwell.com/jobs/1',
    '/jobs/search', '/jobs/abc', '/jobs/1/apply', '/jobs/1\n', '', None])
def test_job_allowlist(url):
    with pytest.raises(ValueError):
        job_from_url(url, {})


def adapter(snapshot, url='https://jobs.forkwell.com/jobs'):
    a = ForkwellSearchAdapter()
    a.page = Mock(url=url)
    a.page.evaluate.return_value = {'ready': 'complete', 'login': False, **snapshot}
    return a


def test_broad_list_routes_and_deduplication():
    a = adapter({'links': ['/jobs/123', '/jobs/123?tracking=fictional', 'https://evil.test/jobs/2']})
    assert [j.job_id for j in a.search_cards('求人一覧', 1)] == ['forkwell:123']
    a.page.goto.assert_called_once_with('https://jobs.forkwell.com/jobs', wait_until='load', timeout=15000)
    assert a.source_url('求人一覧', 2) == 'https://jobs.forkwell.com/jobs/search?page=2'
    for label, page in [('AWS', 1), ('求人一覧', 0), ('求人一覧', True)]:
        with pytest.raises(ValueError): a.source_url(label, page)


@pytest.mark.parametrize('snapshot,reason', [({'login': True}, 'NEEDS_LOGIN'),
    ({'busy': True}, 'PARSE_ERROR'), ({'links': []}, 'NO_VALID_JOB_LINKS')])
def test_list_fail_closed(snapshot, reason):
    a = adapter(snapshot)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人一覧', 1)
    assert exc.value.reason == reason


def test_login_redirect_before_read_and_safe_telemetry(capsys):
    a = adapter({}, 'https://accounts.google.com/signin?private=fictional')
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人一覧', 1)
    assert exc.value.reason == 'NEEDS_LOGIN'
    a.page.evaluate.assert_not_called()
    a.ensure_verified = Mock(side_effect=exc.value)
    assert search_command(SimpleNamespace(platform='forkwell'), None, adapter=a) == 1
    out = capsys.readouterr().err
    assert 'NEEDS_LOGIN' in out and 'private' not in out


def test_detail_fields_and_identity_recheck():
    job = job_from_url('/fictional-company/jobs/123', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    detail = {'title': 'Fictional SRE', 'sections': {'仕事内容': '基盤の運用を担当',
        '年収': '600〜900万円', '勤務地': '架空市', '開発環境': 'Terraform', '必須要件': 'Linux'}}
    a.page.evaluate.side_effect = [snapshot, detail, snapshot]
    fields = a.job_detail(job)
    assert fields['responsibilities'] == '基盤の運用を担当'
    assert fields['required'] == 'Linux'
    assert fields['salary'] == '600〜900万円'
    a.page.evaluate.side_effect = [{**snapshot, 'canonical': 'https://jobs.forkwell.com/jobs/999'}]
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
    config = validate_config({'platform': 'forkwell'})
    assert config['sources'] == ['求人一覧']
    with pytest.raises(ValueError): validate_config({'platform': 'forkwell', 'sources': ['AWS']})
    with pytest.raises(ValueError): validate_config({'platform': 'all'})
    calls = []
    manager = SearchRunManager(runner=lambda argv, env, emit: calls.append(argv) or 0)
    manager._run(config)
    assert calls[0][5:7] == ['forkwell', '--keyword']
    assert manager.snapshot()['exit_code'] == 0


def test_web_badge_and_link():
    job = job_from_url('/jobs/123', {})
    result = SimpleNamespace(verdict='POSSIBLE', summary='待确认', reasons=[], concerns=[])
    card = search_cards([(job, result)])[0]
    assert card['platform_label'] == 'Forkwell' and card['url'] == job.url
    job.job_id = 'forkwell:999'
    assert search_cards([(job, result)])[0]['url'] is None


def test_forkwell_cache_state_and_green_history_preserved(tmp_path):
    from scout_agent.search import SearchStore, SearchEvaluation, run_search
    from scout_agent.storage.db import Database
    from scout_agent.search_platforms import Job
    fields = {'title': 'Fictional SRE', 'responsibilities': 'Cloud 基盤の設計と運用', 'salary': '600〜900万円'}
    a = ForkwellSearchAdapter()
    a.search_cards = Mock(side_effect=lambda *args: [job_from_url('/jobs/123', {})])
    a.job_detail = Mock(return_value=fields)
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.return_value = {'forkwell:123': SearchEvaluation(verdict='POSSIBLE', summary='虚构职位待确认。')}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        green = Job.from_url('/company/900001/job/123', fields)
        store.save_job(green)
        store.set_user_state(green.job_id, 'EXCLUDED')
        store.advance_source('AWS', 7)
        _, stats = run_search(a, store, model, keywords=['求人一覧'], pages_per_keyword=2)
        assert stats['unique_jobs'] == 1 and stats['model_jobs'] == 1
        store.set_user_state('forkwell:123', 'APPLIED')
        _, stats = run_search(a, store, model, keywords=['求人一覧'], pages_per_keyword=2)
        assert stats['cache_hits'] == 1 and stats['model_jobs'] == 0
        assert a.job_detail.call_count == 2  # Broad cards have no hashable JD; details refresh before cache lookup.
        assert model.classify_jobs.call_count == 1
        assert store.user_states() == {green.job_id: 'EXCLUDED', 'forkwell:123': 'APPLIED'}
        assert store.source_cursor('AWS', 15) == 7
        assert store.existing_job(green.job_id).platform == 'green'


@pytest.mark.parametrize('url', ['https://evil.test/jobs', 'https://jobs.forkwell.com/profile',
    'https://jobs.forkwell.com/jobs/search?page=99'])
def test_foreign_and_unexpected_redirects_stop_before_dom_read(url):
    a = adapter({}, url)
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人一覧', 1)
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
    with pytest.raises(GreenSearchDOMPending) as exc: a.search_cards('求人一覧', 1)
    assert exc.value.reason == 'PARSE_ERROR'


def fictional_detail(nodes):
    """Execute the actual DETAIL JS against a fictional, browser-free DOM double."""
    import json
    import subprocess
    from scout_agent.forkwell_discovery import DETAIL

    node = require_node()
    script = r'''
const nodes = INPUT.map(([tag, text, shown = true]) => ({
  tag, textContent: text, innerText: text, shown,
  getClientRects() { return this.shown ? [1] : []; },
  matches(selector) { return selector.split(',').includes(this.tag); }
}));
nodes.forEach((n, i) => n.nextElementSibling = nodes[i + 1] || null);
global.getComputedStyle = () => ({visibility: 'visible'});
global.document = {
  querySelectorAll(selector) { return nodes.filter(n => n.matches(selector)); },
  get body() { throw new Error('Body fallback is forbidden'); }
};
'''.replace('INPUT', json.dumps(nodes))
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + DETAIL + ')()));'],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_detail_h1_sections_and_skill_experience_labels():
    data = fictional_detail([
        ('h1', 'Hidden fictional title', False),
        ('h1', 'Fictional SRE'), ('p', 'Title introduction'),
        ('h1', '業務内容'), ('p', '架空基盤の運用'),
        ('h1', '必須スキル/経験'), ('p', 'Linux'),
        ('h2', '歓迎スキル/経験'), ('p', 'Terraform'),
        ('h2', 'Unapproved heading'), ('p', 'Unrelated text'),
    ])
    assert data['title'] == 'Fictional SRE'
    assert 'Fictional SRE' not in data['sections']
    assert 'Hidden fictional title' not in data['sections']
    fields = parse_fields(data['sections'], data['title'])
    assert fields == {'title': 'Fictional SRE', 'responsibilities': '架空基盤の運用',
                      'required': 'Linux', 'preferred': 'Terraform'}


@pytest.mark.parametrize('label', ['仕事概要', '求人概要'])
def test_detail_summary_responsibilities_fallback(label):
    data = fictional_detail([('h1', 'Fictional SRE'), ('h2', label), ('p', '架空基盤の設計')])
    assert parse_fields(data['sections'], data['title'])['responsibilities'] == '架空基盤の設計'


@pytest.mark.parametrize('labels', [('求人概要', '業務内容'), ('業務内容', '求人概要')])
def test_detail_specific_duties_win_in_either_dom_order(labels):
    content = {'求人概要': '架空企業の紹介', '業務内容': '架空基盤の運用'}
    nodes = [('h1', 'Fictional SRE')]
    for label in labels:
        nodes.extend([('h2', label), ('p', content[label])])
    data = fictional_detail(nodes)
    assert parse_fields(data['sections'], data['title'])['responsibilities'] == '架空基盤の運用'


@pytest.mark.parametrize('title', ['業務内容', 'Fictional SRE'])
def test_detail_title_and_unapproved_headings_cannot_supply_duties(title):
    data = fictional_detail([('h1', title), ('p', 'Introductory text'),
                             ('h2', '業務内容について'), ('p', 'Not an allowed label'),
                             ('h2', '開発環境'), ('p', 'AWS')])
    assert title not in data['sections']
    job = job_from_url('/jobs/123', {})
    a = adapter({}, job.url)
    snapshot = {'ready': 'complete', 'canonical': job.url}
    a.page.evaluate.side_effect = [snapshot, data, snapshot]
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job)
    assert exc.value.reason == 'RESPONSIBILITIES_MISSING'


@pytest.mark.parametrize('required', ['必須スキル', '必須スキル/経験'])
@pytest.mark.parametrize('preferred', ['歓迎スキル', '歓迎スキル/経験'])
def test_detail_fixed_skill_aliases(required, preferred):
    data = fictional_detail([('h1', 'Fictional SRE'), ('h2', '仕事内容'), ('p', '架空基盤の運用'),
                             ('h1', required), ('p', 'Linux'), ('h2', preferred), ('p', 'Terraform')])
    fields = parse_fields(data['sections'], data['title'])
    assert fields['required'] == 'Linux'
    assert fields['preferred'] == 'Terraform'
