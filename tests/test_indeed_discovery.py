"""Fictional, offline Stage A evidence; no browser/model calls."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent import indeed_discovery as ind
from scout_agent.platform_discovery import DEFAULT_PLATFORMS, collect, evidence, main

BASE = 'https://jp.indeed.com'
ID = 'fictional_ID123'


def snapshot(**changes):
    return dict(dict(ready='complete', busy=False, login=False, challenge=False,
                     links=[], pagination=[], canonical=None,
                     selectors=dict.fromkeys(ind.SELECTORS, False),
                     job_posting_present=False, job_posting_keys=dict.fromkeys(ind.JOB_KEYS, False)), **changes)


def row(path='/jobs', **changes):
    return evidence('indeed', BASE + path, snapshot(**changes))


@pytest.mark.parametrize('path,route,shape', [('/jobs?q=PRIVATE&l=PRIVATE', 'jobs-query', '/jobs'),
    ('/q-PRIVATE-l-PRIVATE-求人.html?rq=PRIVATE', 'seo-search', '/q-:query-l-:location-求人.html'),
    ('/q-PRIVATE-l-PRIVATE-%E6%B1%82%E4%BA%BA.html', 'seo-search', '/q-:query-l-:location-求人.html'),
    ('/PRIVATE?PRIVATE=PRIVATE', 'other', 'other'), ('/jobs/', 'other', 'other')])
def test_routes_redacted(path, route, shape):
    result = row(path)
    assert result['source_route'] == route and result['route_path'] == shape
    assert 'PRIVATE' not in json.dumps(result)
    if route == 'other':
        assert result['safe_failure_category'] == 'NO_JOB_LINK_EVIDENCE'


@pytest.mark.parametrize('canonical,valid', [
    (BASE + '/viewjob?jk=' + ID, True), (BASE + '/viewjob?from=PRIVATE&jk=' + ID, True),
    (BASE + '/viewjob?jk=fictional_other', False), (None, False),
    ('https://evil.test/viewjob?jk=' + ID, False),
    (BASE + '/viewjob/?jk=' + ID, False), (BASE + '/viewjob?jk=short', False),
    (BASE + '/viewjob?jk=' + ID + '&jk=' + ID, False)])
def test_identity_current_canonical(canonical, valid):
    result = row('/viewjob?jk=' + ID, canonical=canonical)
    assert result['stable_identity_candidate'] == ('jk' if valid else None)
    assert result['canonical_pattern'] == ('/viewjob?jk=:id' if valid else None)
    assert ID not in json.dumps(result)


@pytest.mark.parametrize('path', ['/viewjob/?jk=' + ID, '/viewjob?jk=short',
    '/viewjob?jk=' + ID + '&jk=' + ID, '/viewjob?jk=', '/rc/clk?jk=' + ID,
    '/viewjob?jk=%21private123', '/viewjob?jk=' + 'x' * 65])
def test_current_exact_boundary(path):
    result = row(path, canonical=BASE + '/viewjob?jk=' + ID)
    assert result['stable_identity_candidate'] is None
    assert 'detail_selectors_present' not in result


def test_links_counts_query_and_pagination():
    links = [BASE + path + '?jk=' + ID + '&PRIVATE=PRIVATE' for path in ['/viewjob', '/rc/clk', '/pagead/clk', '/PRIVATE']]
    links += [BASE + '/viewjob?jk=bad', 'https://evil.test/viewjob?jk=' + ID,
              BASE + '/viewjob?jk=' + ID + '&jk=' + ID]
    result = row('/jobs?q=PRIVATE&l=PRIVATE&start=PRIVATE&sort=&from=&rq=&rsIdx=&PRIVATE=PRIVATE', links=links,
                 pagination=[{'url': BASE + '/jobs?start=PRIVATE', 'kind': 'next'},
                             {'url': BASE + '/q-PRIVATE-l-PRIVATE-求人.html?start=PRIVATE', 'kind': 'page-number'},
                             {'url': BASE + '/PRIVATE?start=PRIVATE', 'kind': 'prev'},
                             {'url': 'https://evil.test/jobs?start=PRIVATE', 'kind': 'prev'}])
    assert result['jk_link_count'] == 4
    assert result['jk_link_kind_counts'] == dict.fromkeys(ind.KINDS, 1)
    assert result['jk_link_kinds'] == list(ind.KINDS)
    assert all(result['query_keys_present'].values())
    assert result['other_query_key_present'] is True
    assert result['pagination_mode'] == 'start-offset-candidate'
    assert result['pagination_candidates'] == ['next', 'page-number']
    for secret in (ID, 'PRIVATE', 'evil', 'https:'):
        assert secret not in json.dumps(result)
    assert row('/jobs?start=PRIVATE')['pagination_mode'] == 'unknown'
    assert row('/jobs', links=[BASE + '/jobs?start=PRIVATE'])['start_parameter_present'] is False
    assert row('/jobs', links=links * 1000)['jk_link_count'] <= 2000


def test_detail_structure_whitelist():
    result = row('/viewjob?jk=' + ID, selectors=dict.fromkeys(ind.SELECTORS, True),
                 job_posting_present=True, job_posting_keys=dict.fromkeys(ind.JOB_KEYS, True))
    assert result['detail_selectors_present'] == dict.fromkeys(ind.SELECTORS, True)
    assert result['job_posting_keys_present'] == dict.fromkeys(ind.JOB_KEYS, True)
    assert 'job_posting_present' not in row('/jobs')


@pytest.mark.parametrize('changes,category', [({'challenge': True}, 'CHALLENGE'),
    ({'login': True}, 'NEEDS_LOGIN'), ({'ready': 'loading'}, 'LOADING'),
    ({'busy': True}, 'LOADING'), ({'links': 'PRIVATE'}, 'READ_FAILED'),
    ({'canonical': 42}, 'READ_FAILED'), ({'challenge': 'PRIVATE'}, 'READ_FAILED'),
    ({'selectors': {}}, 'READ_FAILED'), ({'job_posting_keys': {'PRIVATE': 'PRIVATE'}}, 'READ_FAILED'),
    ({'pagination': [{'url': 42, 'kind': 'next'}]}, 'READ_FAILED')])
def test_fail_closed(changes, category):
    assert row('/viewjob?jk=' + ID, **changes) == {'platform': 'indeed', 'safe_failure_category': category}


@pytest.mark.parametrize('path,category', [('/challenge', 'CHALLENGE'), ('/captcha', 'CHALLENGE'), ('/login', 'NEEDS_LOGIN')])
def test_blocked_route_no_dom(path, category):
    page = Mock(url=BASE + path)
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['indeed']) == [
        {'platform': 'indeed', 'safe_failure_category': category}]
    assert not page.method_calls


@pytest.mark.parametrize('value', [None, [], {'ready': 'complete'}])
def test_malformed_collection(value):
    page = Mock(url=BASE + '/jobs')
    page.evaluate.return_value = value
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['indeed'])[0]['safe_failure_category'] == 'READ_FAILED'
    page.evaluate.assert_called_once_with(ind.SNAPSHOT)
    assert len(page.method_calls) == 1


def test_page_drift():
    class Page:
        url = BASE + '/jobs'
        def evaluate(self, script):
            self.url = BASE + '/viewjob?jk=' + ID
            return snapshot()
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[Page()])]), ['indeed'])[0]['safe_failure_category'] == 'PAGE_CHANGED'


def test_default_and_help(capsys):
    assert DEFAULT_PLATFORMS == ('forkwell', 'findy', 'lapras', 'type', 'doda', 'mynavi')
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0
    assert 'indeed' in capsys.readouterr().out


@pytest.mark.parametrize('mode', ['detail', 'graph', 'invalid-json', 'challenge', 'login', 'loading'])
def test_snapshot_fictional_dom(mode):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM snapshot verification')
    script = r'''
const mode = MODE;
const privateText = 'PRIVATE company title JD salary location';
const posting = {'@type': 'JobPosting', title: privateText, description: privateText, baseSalary: privateText};
const element = {getClientRects: () => [1], matches: () => mode === 'login', getAttribute: () => '', textContent: privateText};
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.location = {href: 'https://jp.indeed.com/viewjob?jk=fictional_ID123', pathname: '/viewjob'};
globalThis.document = {title: mode === 'challenge' ? 'Just a moment...' : privateText,
 readyState: mode === 'loading' ? 'loading' : 'complete',
 querySelector: s => s === 'link[rel="canonical"]' ? {href: location.href} :
   SELECTORS.includes(s) ? element : null,
 querySelectorAll: s => s.includes('input[type="password"]') && mode === 'login' ? [element] :
   s === 'script[type="application/ld+json"]' ? [{textContent: mode === 'invalid-json' ? '{PRIVATE' :
    JSON.stringify(mode === 'graph' ? {'@graph': [posting]} : [posting])}] : []};
'''.replace('MODE', json.dumps(mode)).replace('SELECTORS', json.dumps(ind.SELECTORS))
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + ind.SNAPSHOT + ')()));'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert 'PRIVATE' not in result.stdout
    output = ind.evidence(BASE + '/viewjob?jk=' + ID, data)
    if mode in {'detail', 'graph'}:
        assert output['job_posting_present'] is True
        assert output['job_posting_keys_present']['baseSalary'] is True
        assert output['job_posting_keys_present']['hiringOrganization'] is False
        assert all(output['detail_selectors_present'].values())
    elif mode == 'invalid-json':
        assert output['job_posting_present'] is False
        assert not any(output['job_posting_keys_present'].values())
    else:
        assert output['safe_failure_category'] == {'challenge': 'CHALLENGE', 'login': 'NEEDS_LOGIN', 'loading': 'LOADING'}[mode]


@pytest.mark.parametrize('raises', [False, True])
def test_challenge_redirect(raises):
    class Page:
        url = BASE + '/jobs'
        def evaluate(self, script):
            self.url = BASE + '/challenge?PRIVATE=PRIVATE'
            if raises:
                raise RuntimeError('PRIVATE')
            return snapshot()
    assert collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[Page()])]), ['indeed']) == [
        {'platform': 'indeed', 'safe_failure_category': 'CHALLENGE'}]


def test_snapshot_visible_same_origin_anchor_bound():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM snapshot verification')
    script = r'''
const anchor = (href, visible = true, label = 'PRIVATE', rel = '') => ({href, textContent: label,
 getClientRects: () => visible ? [1] : [], getAttribute: k => k === 'rel' ? rel : null});
const anchors = [anchor('https://evil.test/viewjob?jk=fictional_ID123'),
 anchor('https://jp.indeed.com/viewjob?jk=fictional_hidden', false),
 anchor('https://jp.indeed.com/jobs?start=PRIVATE', true, 'PRIVATE', 'next'),
 ...Array.from({length: 2100}, () => anchor('https://jp.indeed.com/rc/clk?jk=fictional_ID123'))];
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.location = {href: 'https://jp.indeed.com/jobs?q=PRIVATE', pathname: '/jobs'};
globalThis.document = {title: '', readyState: 'complete', querySelector: () => null,
 querySelectorAll: s => s === 'a[href]' ? anchors : []};
'''
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + ind.SNAPSHOT + ')()));'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data['links']) == 2000
    output = ind.evidence(BASE + '/jobs', data)
    assert output['jk_link_count'] == 1999
    assert output['jk_link_kind_counts']['rc-clk'] == 1999
    assert output['pagination_candidates'] == ['next']
    assert 'PRIVATE' not in json.dumps(output) and ID not in json.dumps(output)
