import argparse
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import (
    PLATFORMS, SNAPSHOT, allowed, collect, evidence, local_endpoint, main,
)


@pytest.mark.parametrize('platform', PLATFORMS)
def test_fixed_domains(platform):
    host = sorted(PLATFORMS[platform])[0]
    assert allowed(f'https://{host}/jobs', platform)
    for url in [f'http://{host}/', f'https://{host}.evil.test/',
                f'https://user:secret@{host}/', f'https://{host}:443/',
                'https://evil.test/', 'file:///secret', 'https://[']:
        assert not allowed(url, platform)


def test_redacted_evidence():
    row = evidence('forkwell', 'https://jobs.forkwell.com/search/private-name?token=secret', {
        'links': ['https://jobs.forkwell.com/jobs/123?cursor=private-token',
                  'https://jobs.forkwell.com/company/PersonalName', 'https://evil.test/jobs/2'],
        'labels': ['仕事内容', 'Fictional private JD', 'PersonalName'],
        'ready': 'complete', 'spa': True,
    })
    assert row['candidate_job_link_patterns'] == ['/jobs/:id']
    assert row['stable_id_candidates'] == ['numeric_path_segment']
    assert row['pagination_mode'] == 'cursor_parameter_candidate'
    assert row['visible_section_headings_or_field_labels'] == ['仕事内容']
    assert row['safe_failure_category'] == 'NONE'
    for secret in ['123', 'secret', 'private', 'PersonalName', 'evil']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('snapshot,category', [
    ({'login': True}, 'NEEDS_LOGIN'),
    ({'ready': 'loading'}, 'LOADING'),
    ({'ready': 'complete', 'busy': True}, 'LOADING'),
    ({'ready': 'complete'}, 'NO_JOB_LINK_EVIDENCE'),
])
def test_failure_categories(snapshot, category):
    assert evidence('type', 'https://type.jp/search', snapshot)['safe_failure_category'] == category


def test_collect_reads_only_allowed_existing_tabs():
    good = Mock(url='https://type.jp/search')
    good.evaluate.return_value = {'ready': 'complete'}
    bad = Mock(url='https://evil.test/')
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[good, bad])])
    rows = collect(browser, ['type', 'findy'])
    assert rows[1]['safe_failure_category'] == 'NO_OPEN_TAB'
    good.evaluate.assert_called_once_with(SNAPSHOT)
    assert good.method_calls == [('evaluate', (SNAPSHOT,), {})]
    assert bad.method_calls == []


def test_exception_never_leaks_content():
    page = Mock(url='https://doda.jp/search')
    page.evaluate.side_effect = RuntimeError('private token JD')
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['doda']) == [
        {'platform': 'doda', 'safe_failure_category': 'READ_FAILED'}]


def test_page_change_fails_closed():
    class Page:
        url = 'https://type.jp/search'
        def evaluate(self, script):
            self.url = 'https://evil.test/'
            return {'ready': 'complete'}
    rows = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[Page()])]), ['type'])
    assert rows[0]['safe_failure_category'] == 'PAGE_CHANGED'


@pytest.mark.parametrize('url', ['https://remote.test:9222', 'http://127.0.0.1',
                                  'http://user:pass@localhost:9222',
                                  'http://localhost:9222/?token=secret'])
def test_remote_or_secret_endpoint_rejected(url):
    with pytest.raises(argparse.ArgumentTypeError):
        local_endpoint(url)


def test_endpoint_and_help(capsys):
    assert local_endpoint('http://127.0.0.1:9222') == 'http://127.0.0.1:9222'
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0
    assert '不导航、不登录、0 模型调用' in capsys.readouterr().out


def test_main_mocked_cdp(monkeypatch, capsys):
    page = Mock(url='https://type.jp/search')
    page.evaluate.return_value = {'ready': 'complete', 'links': ['https://type.jp/jobs/123']}
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    chromium = Mock()
    chromium.connect_over_cdp.return_value = browser
    manager = Mock()
    manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=chromium))
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: manager)
    assert main(['--cdp-endpoint', 'http://127.0.0.1:9222', '--platform', 'type']) == 0
    assert json.loads(capsys.readouterr().out)[0]['safe_failure_category'] == 'NONE'
    chromium.connect_over_cdp.assert_called_once_with('http://127.0.0.1:9222', timeout=10000)
    chromium.connect_over_cdp.side_effect = RuntimeError('private endpoint token')
    assert main(['--cdp-endpoint', 'http://127.0.0.1:9222']) == 1
    assert json.loads(capsys.readouterr().out) == {'safe_failure_category': 'CDP_UNAVAILABLE'}


@pytest.mark.parametrize('url', [
    'https://findy-code.io/login?email=fictional&token=secret',
    'https://findy-code.io/users/sign_in',
    'https://accounts.google.com/v3/signin/identifier?state=secret',
])
def test_login_redirect_is_not_domain_or_parse_failure(url):
    from scout_agent.platform_discovery import login_url
    # sign_in is a conventional authentication route too.
    assert login_url(url, 'findy')
    row = evidence('findy', url, {'links': ['https://findy-code.io/jobs/123']})
    assert row == {'platform': 'findy', 'safe_failure_category': 'NEEDS_LOGIN'}
    assert 'secret' not in json.dumps(row)


def test_login_page_stops_before_snapshot():
    page = Mock(url='https://findy-code.io/login')
    rows = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['findy'])
    assert rows[0]['safe_failure_category'] == 'NEEDS_LOGIN'
    assert page.method_calls == []


@pytest.mark.parametrize('raises', [False, True])
def test_redirect_during_read_stops_and_continues_other_platforms(raises):
    class Page:
        url = 'https://findy-code.io/search'
        calls = 0
        def evaluate(self, script):
            self.calls += 1
            self.url = 'https://accounts.google.com/v3/signin?state=fictional-secret'
            if raises:
                raise RuntimeError('fictional-secret')
            return {'ready': 'complete', 'links': ['https://findy-code.io/jobs/123']}
    expired = Page()
    good = Mock(url='https://type.jp/search')
    good.evaluate.return_value = {'ready': 'complete', 'links': ['https://type.jp/jobs/123']}
    rows = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[expired, good])]), ['findy', 'type'])
    assert [r['safe_failure_category'] for r in rows] == ['NEEDS_LOGIN', 'NONE']
    assert expired.calls == 1
    assert good.method_calls == [('evaluate', (SNAPSHOT,), {})]
    assert 'fictional-secret' not in json.dumps(rows)


def test_login_dom_discards_all_job_evidence():
    row = evidence('findy', 'https://findy-code.io/search', {
        'login': True, 'links': ['https://findy-code.io/jobs/123'], 'ready': 'complete',
    })
    assert row == {'platform': 'findy', 'safe_failure_category': 'NEEDS_LOGIN'}
    assert SNAPSHOT.index('if (login) return') < SNAPSHOT.index("querySelectorAll('a[href]')")
    assert 'Googleでログイン' in SNAPSHOT


def test_summary_one_status_per_platform_and_login_precedence():
    from scout_agent.platform_discovery import platform_summary
    rows = [{'platform': p, 'safe_failure_category': c} for p, c in [
        ('findy', 'NONE'), ('findy', 'NEEDS_LOGIN'), ('type', 'NONE'),
        ('doda', 'READ_FAILED'), ('lapras', 'NO_OPEN_TAB'),
    ]]
    summary = platform_summary(rows, ['findy', 'type', 'doda', 'lapras'])
    assert [r['status'] for r in summary] == ['NEEDS_LOGIN', 'OK', 'BLOCKED', 'UNSUPPORTED']
    assert '[NEEDS_LOGIN] platform=findy' in summary[0]['message']


@pytest.mark.parametrize('path,position', [('/jobs/876543', 2), ('/fictional-company/jobs/876543/', 3)])
def test_forkwell_detail_without_links(path, position):
    url = 'https://jobs.forkwell.com' + path
    row = evidence('forkwell', url + '?token=fictional-secret', {
        'ready': 'complete', 'canonical': url + '?private=fictional-secret',
        'labels': ['仕事内容', '開発環境', 'Fictional company JD'],
    })
    assert row['page_kind'] == 'detail'
    assert row['stable_id_path_segment'] == position
    assert row['canonical_url_pattern'] == row['route_path']
    assert row['safe_failure_category'] == 'NONE'
    assert row['visible_section_headings_or_field_labels'] == ['仕事内容', '開発環境']
    for secret in ['876543', 'fictional', 'token', 'private', 'JD']:
        assert secret not in json.dumps(row)


def test_forkwell_list_pagination_redaction():
    base = 'https://jobs.forkwell.com'
    row = evidence('forkwell', base + '/jobs?page=fictional-secret', {
        'ready': 'complete',
        'links': [base + '/fictional-company/jobs/876543', base + '/jobs/876544',
                  base + '/jobs?page=2&fictional-secret=value', 'https://evil.test/jobs/9'],
        'pagination': [{'url': base + '/jobs?page=2', 'kind': 'next'},
                       {'url': base + '/jobs?page=1', 'kind': 'prev'},
                       {'url': base + '/jobs?page=3', 'kind': 'page-number'},
                       {'url': 'https://evil.test/private', 'kind': 'next'},
                       {'url': base + '/private', 'kind': 'fictional-secret'}],
    })
    assert row['page_kind'] == 'list'
    assert row['job_link_count'] == 2
    assert row['job_link_patterns'] == ['/:segment/jobs/:id', '/jobs/:id']
    assert row['pagination_link_patterns'] == ['/jobs']
    assert row['pagination_query_keys'] == ['page']
    assert row['pagination_candidates'] == ['next', 'page-number', 'prev']
    assert row['stable_id_path_segment'] is None
    for secret in ['fictional', '876543', '876544', 'evil', 'private', 'value']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('canonical', ['https://evil.test/jobs/876543',
    'https://jobs.forkwell.com/jobs/999', 'http://jobs.forkwell.com/jobs/876543',
    'https://user:secret@jobs.forkwell.com/jobs/876543'])
def test_forkwell_canonical_fails_closed(canonical):
    row = evidence('forkwell', 'https://jobs.forkwell.com/jobs/876543', {
        'ready': 'complete', 'canonical': canonical})
    assert row['canonical_url_pattern'] is None


@pytest.mark.parametrize('path', ['/jobs', '/jobs/private', '/profile/876543', '/jobs/876543/apply'])
def test_forkwell_other_is_not_detail(path):
    row = evidence('forkwell', 'https://jobs.forkwell.com' + path, {'ready': 'complete'})
    assert row['page_kind'] == 'other'
    assert row['stable_id_path_segment'] is None
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'


def test_forkwell_login_discards_structural_evidence():
    assert evidence('forkwell', 'https://jobs.forkwell.com/jobs/876543', {
        'login': True, 'canonical': 'https://jobs.forkwell.com/jobs/876543',
        'pagination': [{'url': 'https://jobs.forkwell.com/jobs?page=2', 'kind': 'next'}],
    }) == {'platform': 'forkwell', 'safe_failure_category': 'NEEDS_LOGIN'}


def test_forkwell_loading_detail_is_not_success():
    assert evidence('forkwell', 'https://jobs.forkwell.com/jobs/876543', {
        'ready': 'loading'})['safe_failure_category'] == 'LOADING'


def test_snapshot_fictional_dom_pagination_and_label_filtering():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM snapshot verification')
    script = r'''
const anchor = (href, text, rel = '') => ({href, textContent: text,
  getClientRects: () => [1], getAttribute: name => name === 'rel' ? rel : null});
const anchors = [anchor('https://jobs.forkwell.com/jobs?page=2&token=secret', 'Private company', 'next'),
  anchor('https://jobs.forkwell.com/jobs?page=1', '前へ'),
  anchor('https://jobs.forkwell.com/jobs?page=3', '3'),
  anchor('https://jobs.forkwell.com/profile', 'Private profile')];
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.document = {readyState: 'complete',
  querySelector: s => s === 'link[rel="canonical"]' ? {href: 'https://jobs.forkwell.com/jobs/876543'} : null,
  querySelectorAll: s => s === 'a[href]' ? anchors : s === 'h1,h2,h3,h4,dt,label,th' ?
    [anchor('', '仕事内容'), anchor('', 'Private JD')] : []};
'''
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + SNAPSHOT + ')()));'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert [p['kind'] for p in snapshot['pagination']] == ['next', 'prev', 'page-number']
    assert snapshot['labels'] == ['仕事内容']
    assert 'Private' not in result.stdout
    row = evidence('forkwell', 'https://jobs.forkwell.com/jobs/876543', snapshot)
    assert row['canonical_url_pattern'] == '/jobs/:id'
    assert 'secret' not in json.dumps(row)


def test_forkwell_malformed_snapshot_fails_closed():
    page = Mock(url='https://jobs.forkwell.com/jobs/876543')
    page.evaluate.return_value = {'ready': 'complete', 'pagination': 'private-secret'}
    # Invalid structural entries cannot become evidence or leak their content.
    row = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['forkwell'])[0]
    assert row['safe_failure_category'] == 'READ_FAILED'
    assert 'private-secret' not in json.dumps(row)
    page.evaluate.return_value = {'ready': 'complete', 'links': 123}
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['forkwell']) == [
        {'platform': 'forkwell', 'safe_failure_category': 'READ_FAILED'}]
