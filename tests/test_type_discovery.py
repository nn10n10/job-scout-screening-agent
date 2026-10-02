"""Fictional, model-free Stage A Type discovery fixtures."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import TYPE_SNAPSHOT, collect, evidence

BASE = 'https://type.jp'
DETAIL = BASE + '/job-123/456_detail/'
PATTERN = '/job-:id/:id_detail/'


def probe(path='/job-123/456_detail/', **snapshot):
    return evidence('type', BASE + path, {'ready': 'complete', **snapshot})


def test_exact_detail_identity_and_fixed_fields():
    row = probe(canonical=DETAIL + '?token=fictional-secret', labels=['仕事内容', '年収', 'Private JD'])
    assert row['page_kind'] == 'detail'
    assert row['route_path'] == row['canonical_url_pattern'] == PATTERN
    assert row['category_id_path_segment'] == 1
    assert row['job_id_path_segment'] == 2
    assert row['stable_identity_candidate'] == 'category_plus_job_id'
    assert row['stable_id_candidates'] == []
    assert row['visible_section_headings_or_field_labels'] == ['仕事内容', '年収']
    assert row['safe_failure_category'] == 'NONE'
    for secret in ['123', '456', 'fictional-secret', 'Private JD']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('canonical', [None, BASE + '/job-123/999_detail/',
    BASE + '/job-999/456_detail/', 'https://evil.test/job-123/456_detail/',
    'http://type.jp/job-123/456_detail/', BASE + '/job-123/456_detail'])
def test_canonical_requires_same_exact_path(canonical):
    row = probe(canonical=canonical)
    assert row['stable_identity_candidate'] is None
    assert row['canonical_url_pattern'] is None


@pytest.mark.parametrize('path,kind', [('/job/search/', 'search-entry'),
    ('/job/foo/', 'other'), ('/job-123/', 'other'), ('/job-123/456_detail/extra', 'other')])
def test_non_detail_routes_never_prove_identity(path, kind):
    row = probe(path, canonical=BASE + path)
    assert row['page_kind'] == kind
    assert row['stable_identity_candidate'] is None
    assert row['canonical_url_pattern'] is None
    assert row['category_id_path_segment'] is None
    if kind == 'search-entry':
        assert row['route_path'] == path
        assert row['safe_failure_category'] == 'NONE'


def test_list_dedup_classification_and_pagination_redaction():
    row = probe('/', links=[DETAIL, DETAIL, DETAIL + '?token=private-value',
        DETAIL + '#private-fragment', BASE + '/job-123/789_detail/', BASE + '/job/search/',
        BASE + '/job/fictional-opaque/', BASE + '/job-123/',
        BASE + '/job/search/?pageNo=private-value&private-key=private-value',
        'https://evil.test/job-123/555_detail/'],
        pagination=[{'url': BASE + '/job/search/?cursor=private-value', 'kind': 'next'},
                    {'url': 'https://evil.test/?offset=private-value', 'kind': 'prev'},
                    {'url': BASE + '/', 'kind': 'private-kind'}],
        controls=['load-more', 'private-control'], labels=['給与', 'Private JD'])
    assert row['page_kind'] == 'list'
    assert row['job_link_count'] == 2
    assert row['job_link_patterns'] == [PATTERN]
    assert row['search_entry_link_patterns'] == ['/job/search/']
    assert row['candidate_job_link_patterns'] == ['/job-:id/', PATTERN, '/job/:segment/']
    assert row['pagination_query_keys'] == ['cursor', 'pageNo']
    assert row['pagination_link_patterns'] == ['/job/search/']
    assert row['pagination_candidates'] == ['load-more', 'next']
    assert row['stable_identity_candidate'] is None
    for secret in ['123', '456', '789', '555', 'private', 'fictional', 'evil', 'Private JD']:
        assert secret not in json.dumps(row)


@pytest.mark.parametrize('snapshot', [None, [], {'links': 'private'}, {'labels': [123]},
    {'controls': {}}, {'pagination': [{}]}, {'canonical': 123}])
def test_malformed_fails_closed(snapshot):
    page = Mock(url=DETAIL)
    page.evaluate.return_value = snapshot
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['type']) == [
        {'platform': 'type', 'safe_failure_category': 'READ_FAILED'}]
    assert page.method_calls == [('evaluate', (TYPE_SNAPSHOT,), {})]


def test_login_loading_and_foreign_fail_closed():
    assert probe(login=True) == {'platform': 'type', 'safe_failure_category': 'NEEDS_LOGIN'}
    assert probe(ready='loading')['safe_failure_category'] == 'LOADING'
    assert probe(busy=True)['safe_failure_category'] == 'LOADING'
    assert evidence('type', 'https://evil.test/', {})['safe_failure_category'] == 'DOMAIN_BLOCKED'


def test_type_reads_all_visible_anchors_without_changing_generic_snapshot():
    from scout_agent.platform_discovery import SNAPSHOT
    assert '.slice(0, 2000)' not in TYPE_SNAPSHOT
    assert '.slice(0, 2000)' in SNAPSHOT
