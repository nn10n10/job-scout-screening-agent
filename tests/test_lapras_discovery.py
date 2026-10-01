"""Fictional, offline LAPRAS structural probe evidence."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import SNAPSHOT, LAPRAS_DETAIL_SNAPSHOT, collect, evidence

BASE = 'https://lapras.com'


def probe(path='/recommendations', **snapshot):
    return evidence('lapras', BASE + path, {'ready': 'complete', **snapshot})


@pytest.mark.parametrize('path,pattern,stable', [
    ('/jobs/876543', '/jobs/:id', 2),
    ('/jobs/fictional-job', '/jobs/:segment', None),
])
def test_detail_canonical_and_id_redaction(path, pattern, stable):
    row = probe(path + '?token=fictional-secret', canonical=BASE + path,
                labels=['仕事内容', '応募資格', 'Fictional company JD'])
    assert row['page_kind'] == 'detail'
    assert row['job_link_count'] == 0
    assert row['stable_id_path_segment'] == stable
    assert row['canonical_url_pattern'] == pattern
    assert row['safe_failure_category'] == 'NONE'
    assert row['visible_section_headings_or_field_labels'] == ['仕事内容', '応募資格']
    for secret in ['876543', 'fictional', 'token', 'Fictional', 'JD']:
        assert secret not in json.dumps(row)


def test_list_pagination_candidates_and_redaction():
    row = probe('/recommendations?cursor=fictional-secret', links=[
        BASE + '/jobs/876543', BASE + '/jobs/fictional-job',
        BASE + '/jobs/876543/apply', 'https://evil.test/jobs/876544',
    ], pagination=[
        {'url': BASE + '/recommendations?page=2&token=secret', 'kind': 'next'},
        {'url': BASE + '/recommendations?p=1', 'kind': 'prev'},
        {'url': BASE + '/recommendations?offset=3', 'kind': 'page-number'},
        {'url': BASE + '/recommendations', 'kind': 'load-more'},
        {'url': 'https://evil.test/jobs?page=1', 'kind': 'next'},
        {'url': BASE + '/private', 'kind': 'fictional-secret'},
    ], controls=['load-more', 'fictional-secret'])
    assert row['page_kind'] == 'list'
    assert row['job_link_count'] == 2
    assert row['job_link_patterns'] == ['/jobs/:id', '/jobs/:segment']
    assert row['stable_id_path_segment'] is None
    assert row['pagination_link_patterns'] == ['/:segment']
    assert row['pagination_query_keys'] == ['cursor', 'offset', 'p', 'page']
    assert row['pagination_candidates'] == ['load-more', 'next', 'page-number', 'prev']
    assert row['safe_failure_category'] == 'NONE'
    for secret in ['876543', '876544', 'fictional', 'secret', 'token', 'evil']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('url', [
    'http://lapras.com/jobs/876543', 'https://lapras.com.evil.test/jobs/876543',
    'https://user:secret@lapras.com/jobs/876543', 'https://lapras.com:443/jobs/876543',
    'https://evil.test/jobs/876543', 'https://lapras.com/jobs/999',
])
def test_canonical_whitelist_and_same_job(url):
    assert probe('/jobs/876543', canonical=url)['canonical_url_pattern'] is None
    if '/jobs/999' not in url:
        assert evidence('lapras', url, {})['safe_failure_category'] == 'DOMAIN_BLOCKED'


@pytest.mark.parametrize('path', ['/jobs', '/jobs/search', '/profile/876543',
                                   '/jobs/876543/apply', '/company/jobs/876543'])
def test_other_routes_fail_closed(path):
    row = probe(path)
    assert row['page_kind'] == 'other'
    assert row['stable_id_path_segment'] is None
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'


def test_unrecognized_job_links_do_not_make_list_evidence():
    row = probe(links=[BASE + '/jobs/876543/apply', BASE + '/jobs/search',
                       'https://evil.test/jobs/876543'])
    assert row['job_link_count'] == 0
    assert row['candidate_job_link_patterns'] == []
    assert row['stable_id_candidates'] == []
    assert row['page_kind'] == 'other'
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'


@pytest.mark.parametrize('snapshot,category', [
    ({'login': True}, 'NEEDS_LOGIN'), ({'ready': 'loading'}, 'LOADING'),
    ({'busy': True}, 'LOADING'),
])
def test_login_and_loading(snapshot, category):
    row = probe('/jobs/876543', **snapshot)
    assert row['safe_failure_category'] == category
    if category == 'NEEDS_LOGIN':
        assert row == {'platform': 'lapras', 'safe_failure_category': category}


@pytest.mark.parametrize('snapshot', [None, {'links': 123}, {'pagination': 'secret'},
                                       {'controls': 'secret'}])
def test_malformed_snapshot_read_failed(snapshot):
    page = Mock(url=BASE + '/jobs/876543')
    page.evaluate.return_value = snapshot
    rows = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['lapras'])
    assert rows == [{'platform': 'lapras', 'safe_failure_category': 'READ_FAILED'}]
    assert page.method_calls == [('evaluate', (LAPRAS_DETAIL_SNAPSHOT,), {})]


def test_login_route_not_read_and_redirect_needs_login():
    page = Mock(url=BASE + '/login?token=secret')
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    assert collect(browser, ['lapras']) == [
        {'platform': 'lapras', 'safe_failure_category': 'NEEDS_LOGIN'}]
    assert page.method_calls == []
    page.url = BASE + '/jobs/876543'

    def redirect(script):
        page.url = 'https://accounts.google.com/signin?token=secret'
        return {'ready': 'complete'}

    page.evaluate.side_effect = redirect
    assert collect(browser, ['lapras']) == [
        {'platform': 'lapras', 'safe_failure_category': 'NEEDS_LOGIN'}]


def test_changed_page_not_evidence():
    page = Mock(url=BASE + '/recommendations')

    def change(script):
        page.url = BASE + '/jobs/876543'
        return {'ready': 'complete'}

    page.evaluate.side_effect = change
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['lapras']) == [
        {'platform': 'lapras', 'safe_failure_category': 'PAGE_CHANGED'}]


def test_snapshot_load_more_fictional_dom():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM snapshot verification')
    script = r'''
const control = (text, visible = true) => ({textContent: text,
  getClientRects: () => visible ? [1] : [], matches: () => false,
  getAttribute: () => null});
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.document = {readyState: 'complete', querySelector: () => null,
  querySelectorAll: s => s === 'a[href]' ?
    [{...control('もっと見る'), href: 'https://lapras.com/recommendations?cursor=secret'}] :
    s === 'button,[role="button"]' ?
    [control('もっと見る'), control('Load more'), control('Private company'),
     control('さらに表示', false)] : []};
'''
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + SNAPSHOT + ')()));'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot['controls'] == ['load-more', 'load-more']
    assert snapshot['pagination'] == [
        {'url': BASE + '/recommendations?cursor=secret', 'kind': 'load-more'}]
    assert 'Private' not in result.stdout
    snapshot['links'] = [BASE + '/jobs/876543']
    assert probe(**snapshot)['pagination_candidates'] == ['load-more']
    assert 'secret' not in json.dumps(probe(**snapshot))


@pytest.mark.parametrize('path', ['/jobs/home', '/jobs/search'])
def test_source_routes_are_not_job_candidates(path):
    row = probe(path, links=[BASE + '/jobs/home', BASE + '/jobs/search',
                            BASE + '/jobs/876543', BASE + '/jobs/fictional-job'])
    assert row['route_path'] == path
    assert row['page_kind'] == 'list'
    assert row['stable_id_path_segment'] is None
    assert row['canonical_url_pattern'] is None
    assert row['job_link_count'] == 2
    assert row['job_link_patterns'] == ['/jobs/:id', '/jobs/:segment']


def test_home_without_links_is_list_but_fails_closed():
    row = probe('/jobs/home', links=[BASE + '/jobs/home', BASE + '/jobs/search'])
    assert row['page_kind'] == 'list'
    assert row['job_link_count'] == 0
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'
