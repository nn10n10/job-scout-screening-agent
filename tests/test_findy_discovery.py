"""Fictional offline Findy discovery; no browser or network access."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import SNAPSHOT, collect, evidence, main

BASE = 'https://findy-code.io'
DETAIL = '/companies/123/jobs/opaque-key'


def probe(path='/recommends', **snapshot):
    return evidence('findy', BASE + path, {'ready': 'complete', **snapshot})


def test_source_links_and_redaction():
    row = probe('/recommends?page=private-value', links=[
        BASE + DETAIL, BASE + '/companies/456/jobs/another-key',
        BASE + '/companies/123/jobs', BASE + DETAIL + '/apply',
        'https://foreign.test' + DETAIL],
        labels=['仕事内容', '勤務時間', 'Fictional company JD'],
        pagination=[{'url': BASE + '/recommends?page=secret', 'kind': 'next'},
                    {'url': BASE + '/recommends?cursor=secret', 'kind': 'load-more'},
                    {'url': 'https://foreign.test/?offset=secret', 'kind': 'prev'}],
        controls=['load-more', 'private text'])
    assert row['page_kind'] == 'list'
    assert row['route_path'] == '/recommends'
    assert row['job_link_count'] == 2
    assert row['job_link_patterns'] == ['/companies/:id/jobs/:segment']
    assert row['company_id_path_segment'] == 2
    assert row['job_key_path_segment'] is None
    assert row['stable_identity_candidate'] is None
    assert row['stable_id_candidates'] == []
    assert row['pagination_query_keys'] == ['cursor', 'page']
    assert row['pagination_link_patterns'] == ['/recommends']
    assert row['pagination_candidates'] == ['load-more', 'next']
    for secret in ['123', '456', 'opaque-key', 'another-key', 'private', 'secret', 'Fictional', 'JD', 'foreign']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('key', ['opaque-key', '987654', 'jobs'])
def test_exact_detail_same_canonical(key):
    path = '/companies/123/jobs/' + key
    row = probe(path + '?token=private-value', canonical=BASE + path)
    assert row['page_kind'] == 'detail'
    assert row['safe_failure_category'] == 'NONE'
    assert row['company_id_path_segment'] == 2
    assert row['job_key_path_segment'] == 4
    assert row['job_key_kind'] == 'opaque_segment'
    assert row['canonical_url_pattern'] == '/companies/:id/jobs/:segment'
    assert row['route_path'] == '/companies/:id/jobs/:segment'
    assert row['stable_identity_candidate'] == 'company_id_plus_job_key'
    assert row['stable_id_candidates'] == []
    for secret in ['123', '987654', 'opaque-key', 'token', 'private-value']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('canonical', [None, BASE + '/companies/456/jobs/opaque-key',
    BASE + '/companies/123/jobs/different-key', 'https://foreign.test' + DETAIL,
    BASE + DETAIL + '/', 'http://findy-code.io' + DETAIL])
def test_canonical_mismatch(canonical):
    row = probe(DETAIL, canonical=canonical)
    assert row['stable_identity_candidate'] is None
    assert row['canonical_url_pattern'] is None


@pytest.mark.parametrize('path', ['/companies/123/jobs', '/companies/123', '/recommends/',
    '/search', DETAIL + '/apply', '/companies/company/jobs/key',
    '/companies/123/jobs/key%2Fextra', '/companies/123/jobs/..'])
def test_other_fail_closed_even_with_links(path):
    row = probe(path, links=[BASE + DETAIL], canonical=BASE + path)
    assert row['page_kind'] == 'other'
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'
    assert row['stable_identity_candidate'] is None
    assert row['stable_id_candidates'] == []


def test_empty_source_keeps_list_kind():
    row = probe()
    assert row['page_kind'] == 'list'
    assert row['job_link_count'] == 0
    assert row['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'


@pytest.mark.parametrize('snapshot', [None, [], {'links': 'secret'}, {'labels': [123]},
    {'pagination': 'secret'}, {'pagination': ['secret']},
    {'pagination': [{'url': BASE, 'kind': []}]}, {'controls': 'secret'},
    {'canonical': 123}])
def test_malformed_read_failed_and_read_only(snapshot):
    page = Mock(url=BASE + '/recommends')
    page.evaluate.return_value = snapshot
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['findy']) == [
        {'platform': 'findy', 'safe_failure_category': 'READ_FAILED'}]
    assert page.method_calls == [('evaluate', (SNAPSHOT,), {})]


def test_mocked_cli_findy(monkeypatch, capsys):
    page = Mock(url=BASE + DETAIL)
    page.evaluate.return_value = {'ready': 'complete', 'canonical': BASE + DETAIL}
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    chromium = Mock()
    chromium.connect_over_cdp.return_value = browser
    manager = Mock()
    manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=chromium))
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: manager)
    assert main(['--cdp-endpoint', 'http://127.0.0.1:9222', '--platform', 'findy']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)[0]['stable_identity_candidate'] == 'company_id_plus_job_key'
    assert json.loads(output.err)['status'] == 'OK'
    assert page.method_calls == [('evaluate', (SNAPSHOT,), {})]
