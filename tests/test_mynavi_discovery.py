import json
from unittest.mock import Mock

import pytest

from scout_agent.platform_discovery import evidence, collect, MYNAVI_SNAPSHOT
from scout_agent.mynavi_discovery import SELECTORS, PATTERN

BASE = 'https://tenshoku.mynavi.jp'
DETAIL = '/jobinfo-1234-5678-9012-3456/'


def probe(path=DETAIL, **snapshot):
    return evidence('mynavi', BASE + path, {'ready': 'complete', **snapshot})


@pytest.mark.parametrize('canonical,same', [(BASE + DETAIL, True),
    (BASE + DETAIL + '?utm_source=private-value', True),
    (BASE + '/jobinfo-1234-5678-9012-9999/', False), (None, False),
    ('https://foreign.test' + DETAIL, False)])
def test_canonical(canonical, same):
    row = probe(canonical=canonical)
    assert row['page_kind'] == 'jobinfo-detail'
    assert row['stable_identity_candidate'] == ('jobinfo-id4' if same else None)
    assert row['canonical_url_pattern'] == (PATTERN if same else None)
    assert '1234' not in json.dumps(row)
    assert 'private-value' not in json.dumps(row)


def test_anchor_query_redaction_and_limit():
    row = probe(links=[BASE + DETAIL, BASE + DETAIL + '?utm_source=secret&utm_medium=secret&utm_campaign=secret&ty=&private-key=secret#secret',
                      BASE + '/job/fictional-private/', 'https://foreign.test' + DETAIL])
    assert row['jobinfo_link_count'] == 2
    assert row['job_link_count'] == 1
    assert row['fixed_anchor_patterns'] == [PATTERN, '/job/:segment/']
    assert all(row['jobinfo_query_keys_present'].values())
    assert row['other_query_key_present']
    assert row['jobinfo_query_present_count'] == row['jobinfo_fragment_present_count'] == 1
    assert not any(v in json.dumps(row) for v in ['secret', 'private-key', 'fictional-private', '1234'])
    assert probe(links=[BASE + '/other/'] * 2000 + [BASE + DETAIL])['jobinfo_link_count'] == 0


@pytest.mark.parametrize('path,shape', [('/engineer/list/fake-criteria/', '/engineer/list/:criteria/'),
    ('/engineer/ft/fake-criteria/', '/engineer/ft/:criteria/'),
    ('/fake-criteria/other-private/', '/:segment/:segment/')])
@pytest.mark.parametrize('suffix', ['', 'pg2/', 'pg99/'])
def test_source_shapes(path, shape, suffix):
    row = probe(path + suffix)
    assert row['page_kind'] == 'source-list-candidate'
    assert row['source_route_shape'] == shape
    assert row['has_pg_suffix'] == bool(suffix)
    assert row['page_suffix_present'] == bool(suffix)
    assert not any(v in json.dumps(row) for v in ['fake-criteria', 'other-private', 'pg2', 'pg99'])


def test_pagination_only_visible_marked_anchors_and_current_path():
    row = probe('/other/', links=[BASE + '/engineer/list/fake/pg99/'],
                pagination=[{'url': BASE + '/engineer/list/fake/pg2/', 'kind': 'next'}])
    assert row['pagination_mode'] == 'path-pg-candidate'
    assert probe('/other/', links=[BASE + '/pg99/'])['pagination_mode'] == 'unknown'
    assert probe('/other/', pagination=[{'url': 'https://foreign.test/pg2/', 'kind': 'next'}])['pagination_mode'] == 'unknown'


def test_detail_structure_bool_only():
    structure = dict.fromkeys(SELECTORS, True)
    assert probe(mynavi_detail_structure=structure)['mynavi_detail_structure'] == structure
    assert '.textContent' not in MYNAVI_SNAPSHOT.split('snapshot.mynavi_detail_structure')[1]


@pytest.mark.parametrize('snapshot', [None, [], {'links': 'private'}, {'labels': [123]},
    {'canonical': 123}, {'ready': []}, {'busy': 'false'},
    {'pagination': [{}]}, {'mynavi_detail_structure': {'h1 .occName': 'private'}}])
def test_malformed_collect_fails_closed(snapshot):
    page = Mock(url=BASE + DETAIL)
    page.evaluate.return_value = snapshot
    browser = Mock(contexts=[Mock(pages=[page])])
    assert collect(browser, ['mynavi']) == [{'platform': 'mynavi', 'safe_failure_category': 'READ_FAILED'}]
    page.evaluate.assert_called_once_with(MYNAVI_SNAPSHOT)
    page.goto.assert_not_called()
    page.click.assert_not_called()


@pytest.mark.parametrize('path', [DETAIL.rstrip('/'), DETAIL + 'extra/', '/jobinfo-1-2-3/', '/jobinfo-a-2-3-4/'])
def test_exact_match(path):
    assert probe(path, canonical=BASE + path)['stable_identity_candidate'] is None


@pytest.mark.parametrize('snapshot,category', [({'login': True}, 'NEEDS_LOGIN'),
    ({'ready': 'loading'}, 'LOADING'), ({'busy': True}, 'LOADING')])
def test_fail_closed(snapshot, category):
    row = probe(canonical=BASE + DETAIL, **snapshot)
    assert row == {'platform': 'mynavi', 'safe_failure_category': category}


def test_snapshot_fictional_dom_read_only():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM verification')
    script = r'''
const anchor = (href, text, visible = true) => ({href, textContent: text,
  getClientRects: () => visible ? [1] : [], getAttribute: () => null});
const anchors = [anchor('https://tenshoku.mynavi.jp/jobinfo-1234-5678-9012-3456/?ty=private', 'Private title'),
  anchor('https://tenshoku.mynavi.jp/engineer/list/private/pg99/', '99'),
  anchor('https://tenshoku.mynavi.jp/job/hidden-private/', 'Private title', false)];
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.document = {readyState: 'complete',
  querySelector: s => {
    if (s === 'link[rel="canonical"]') return {href: 'https://tenshoku.mynavi.jp/jobinfo-1234-5678-9012-3456/'};
    if (s === 'h1 .occName' || s === 'table.jobOfferTable')
      return {get textContent() {throw new Error('detail text must not be read');}};
    return null;
  },
  querySelectorAll: s => s === 'a[href]' ? anchors : s === 'h1,h2,h3,h4,dt,label,th' ?
    [anchor('', '仕事内容'), anchor('', 'Private JD')] : []};
'''
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + MYNAVI_SNAPSHOT + ')()));'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert len(snapshot['links']) == 2
    assert snapshot['labels'] == ['仕事内容']
    assert snapshot['mynavi_detail_structure'] == {
        selector: selector in {'h1 .occName', 'table.jobOfferTable'} for selector in SELECTORS}
    row = probe(**snapshot)
    assert row['jobinfo_link_count'] == 1
    assert row['job_link_count'] == 0
    assert row['page_suffix_present']
    assert not any(s in json.dumps(row) for s in ['private', 'Private', '1234', 'pg99'])


def test_source_count_capped_and_fixed_tokens_only():
    row = probe('/engineer/list/ft/search/' + '/'.join(['fake-private'] * 20) + '/')
    assert row['nonempty_segment_count_capped'] == 10
    assert row['fixed_segments_present'] == ['engineer', 'ft', 'list', 'search']
    assert row['source_route_shape'] == 'other'
    assert 'fake-private' not in json.dumps(row)
