import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import (
    DODA_DETAIL_PATTERN, DODA_LIST_PATTERN, DODA_PAGE_PATTERN, SNAPSHOT,
    collect, evidence,
)

BASE = 'https://doda.jp'
LIST = BASE + '/DodaFront/View/JobSearchList/'


def detail(jid='987654321', tab='pr'):
    return BASE + f'/DodaFront/View/JobSearchDetail/j_jid__{jid}/-tab__{tab}/'


def row(url, **snapshot):
    return evidence('doda', url, {'ready': 'complete', **snapshot})


@pytest.mark.parametrize('suffix,paged', [('', False), ('fictional-criteria/', False),
    ('fictional-criteria/-page__765432/', True), ('-page__765432/', True),
    ('-page__secret/', False), ('-page__765432/extra/', False)])
def test_list_route_and_page_suffix(suffix, paged):
    result = row(LIST + suffix + '?page=secret&token=private')
    assert result['page_kind'] == 'list'
    assert result['route_path'] == DODA_LIST_PATTERN
    assert result['page_suffix_present'] is paged
    assert result['pagination_candidates'] == (['page-number'] if paged else [])
    assert result['pagination_link_patterns'] == ([DODA_PAGE_PATTERN] if paged else [])
    assert result['stable_identity_candidate'] is None
    for private in ['fictional', '765432', 'secret', 'private', 'token=']:
        assert private not in json.dumps(result)


@pytest.mark.parametrize('tab,kind', [('pr', 'detail-preview'), ('jd', 'detail-jd'),
                                      ('fictional', 'other')])
def test_detail_tabs(tab, kind):
    result = row(detail(tab=tab))
    assert result['page_kind'] == kind
    assert result['route_path'] == DODA_DETAIL_PATTERN
    assert result['detail_tab'] == (tab if tab in {'pr', 'jd'} else 'other')
    assert '987654321' not in json.dumps(result)


@pytest.mark.parametrize('canonical,same', [(detail(tab='jd'), True), (detail(), True),
    (detail('123456789'), False), (None, False),
    (detail().replace(BASE, 'https://foreign.test'), False),
    (detail().replace('https:', 'http:'), False), (LIST, False),
    (detail() + 'extra/', False)])
def test_canonical_identity(canonical, same):
    result = row(detail(), canonical=canonical)
    assert result['stable_identity_candidate'] == ('jid' if same else None)
    assert result['canonical_url_pattern'] == (DODA_DETAIL_PATTERN if same else None)


def test_links_dedup_tabs_and_redaction():
    result = row(LIST + 'fictional-criteria/', links=[
        detail(), detail() + '?token=private', detail(tab='jd'),
        detail('123456789', 'fictional'), detail().replace(BASE, 'https://foreign.test'),
        detail() + 'extra/', LIST + 'fictional-criteria/-page__765432/'],
        labels=['仕事内容', 'Fictional Company', 'Fictional Job JD'],
        pagination=[{'url': LIST + '-page__765432/', 'kind': 'next'}])
    assert result['job_link_count'] == 3
    assert result['job_link_patterns'] == [DODA_DETAIL_PATTERN]
    assert result['job_link_tabs'] == ['jd', 'other', 'pr']
    assert result['same_jid_pr_jd_present'] is True
    assert result['page_suffix_present'] is True
    assert result['visible_section_headings_or_field_labels'] == ['仕事内容']
    for private in ['987654321', '123456789', '765432', 'fictional', 'Fictional',
                    'private', 'foreign', 'Company', 'Job JD', 'https://']:
        assert private not in json.dumps(result)
    assert row(LIST, links=[detail(), detail('123456789', 'jd')])['same_jid_pr_jd_present'] is False


@pytest.mark.parametrize('path', ['/DodaFront/View/JobSearchDetail/j_jid__abc/-tab__pr/',
    '/DodaFront/View/JobSearchDetail/j_jid__987654321/-tab__pr',
    '/DodaFront/View/JobSearchList', '/unknown/'])
def test_route_drift(path):
    result = row(BASE + path, links=[detail()])
    assert result['page_kind'] == 'other'
    assert result['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'
    assert result['stable_identity_candidate'] is None


@pytest.mark.parametrize('snapshot,category', [({'login': True}, 'NEEDS_LOGIN'),
    ({'ready': 'loading'}, 'LOADING'), ({'busy': True}, 'LOADING')])
def test_login_loading(snapshot, category):
    result = row(detail(), canonical=detail(), **snapshot)
    assert result['safe_failure_category'] == category
    assert result.get('stable_identity_candidate') is None
    assert result.get('canonical_url_pattern') is None


@pytest.mark.parametrize('snapshot', [None, {'links': 'private'}, {'labels': [123]},
    {'canonical': 123}, {'pagination': [{'url': 123, 'kind': 'next'}]},
    {'ready': 'complete', 'busy': 'false'}, {'spa': 1}, {'controls': [None]}])
def test_malformed_collect(snapshot):
    page = Mock(url=detail())
    page.evaluate.return_value = snapshot
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['doda']) == [
        {'platform': 'doda', 'safe_failure_category': 'READ_FAILED'}]
    assert page.method_calls == [('evaluate', (SNAPSHOT,), {})]


@pytest.mark.parametrize('tab', ['other', 'secret-tab', 'JD'])
def test_other_tab_never_establishes_identity(tab):
    result = row(detail(tab=tab), canonical=detail(tab='jd'))
    assert result['detail_tab'] == 'other'
    assert result['page_kind'] == 'other'
    assert result['stable_identity_candidate'] is None
    if tab != 'other':
        assert tab not in json.dumps(result)


def test_foreign_and_non_exact_links_are_not_evidence():
    result = row(LIST, links=[detail().replace('https:', 'http:'),
        detail().replace(BASE, 'https://doda.jp.evil.test'),
        detail().replace('987654321', 'not-numeric'), detail() + 'extra/'],
        pagination=[{'url': LIST.replace(BASE, 'https://foreign.test') + '-page__765432/',
                     'kind': 'next'}])
    assert result['job_link_count'] == 0
    assert result['job_link_tabs'] == []
    assert result['same_jid_pr_jd_present'] is False
    assert result['page_suffix_present'] is False


def test_unknown_route_has_only_fixed_pattern():
    result = row(BASE + '/jobs/987654321/FictionalCompany?criteria=private')
    assert result['route_path'] == '/:unrecognized'
    assert result['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'
    for private in ['987654321', 'FictionalCompany', 'private', 'criteria']:
        assert private not in json.dumps(result)
