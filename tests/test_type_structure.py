"""Fictional DOM and mock CLI only; no external services or user data."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search import search_command
from scout_agent.type_search import TypeSearchAdapter
from scout_agent.type_structure import COUNTS, SOURCE_STRUCTURE, sanitize
from scout_agent.web.search_runs import SearchRunManager


def unsafe():
    secret = 'https://type.jp/job-987654/876543_detail/?private=SECRET_SLUG'
    return dict(route_kind=secret, ready=secret, busy=secret,
                visible_anchor_count_capped=9999, exact_detail_link_count=True,
                has_canonical=secret, canonical_kind=secret,
                visible_fixed_labels=['仕事内容', secret, '仕事内容', {}, 987654],
                url=secret, id=secret, **{'class': secret, 'data-private': secret})


def test_sanitizer_rebuilds_schema():
    result = sanitize(unsafe())
    assert set(result) == {'route_kind', 'ready', 'busy', *COUNTS,
                           'has_canonical', 'canonical_kind', 'visible_fixed_labels'}
    assert result['route_kind'] == 'other'
    assert result['ready'] == 'loading'
    assert result['visible_anchor_count_capped'] == 2000
    assert result['exact_detail_link_count'] == 0
    assert result['visible_fixed_labels'] == ['仕事内容']
    assert result['canonical_kind'] == 'other'
    output = json.dumps(result)
    for secret in ('987654', '876543', 'SECRET_SLUG', 'https:', 'private', 'class', 'data-'):
        assert secret not in output
    for value in (-1, True, '987654', None, {}, float('inf')):
        assert sanitize(dict.fromkeys(COUNTS, value))['same_origin_anchor_count'] == 0


@pytest.mark.parametrize('route', ['job-category', 'job-search', 'other'])
@pytest.mark.parametrize('canonical', ['job-category', 'job-search', 'detail', 'other', 'none'])
def test_fixed_enums(route, canonical):
    result = sanitize({'route_kind': route, 'canonical_kind': canonical})
    assert result['route_kind'] == route
    assert result['canonical_kind'] == canonical


@pytest.mark.parametrize('path,route', [('/job-1/', 'job-category'), ('/job/search/', 'job-search'), ('/private/', 'other')])
@pytest.mark.parametrize('canonical,kind', [('/job-987654/', 'job-category'), ('/job/search/', 'job-search'),
    ('/job-987654/876543_detail/', 'detail'), ('https://fictional.invalid/private', 'other'), (None, 'none')])
def test_js_counts_only(path, route, canonical, kind):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM test')
    harness = r'''
 const hrefs = ['/job-987654/876543_detail/', '/job-987654/', '/job/SECRET_SLUG/', '/job/search/',
 '/job-987654/876543_detail/?private=SECRET_QUERY', 'https://fictional.invalid/job-987654/', '/job-987654/876543_detail/'];
 const el = (href, shown=true) => ({getAttribute: () => href, getClientRects: () => shown ? [1] : [], textContent: 'PRIVATE_TEXT'});
 const anchors = hrefs.map((h, i) => el(h, i !== 6));
 globalThis.getComputedStyle = () => ({visibility: 'visible'});
 globalThis.location = {origin: 'https://type.jp', pathname: PATH, href: 'https://type.jp' + PATH};
 globalThis.document = {readyState: 'complete',
 querySelector: () => CANONICAL === null ? null : el(CANONICAL),
 querySelectorAll: selector => selector === 'a[href]' ? anchors : selector.includes('aria-busy') ? [] :
 [{...el(''), textContent: '仕事内容'}, {...el(''), textContent: 'PRIVATE_TEXT'}]};
 '''.replace('PATH', json.dumps(path)).replace('CANONICAL', json.dumps(canonical))
    proc = subprocess.run([node, '-e', harness + '\nconsole.log(JSON.stringify((' + SOURCE_STRUCTURE + ')()));'],
                          capture_output=True, text=True, check=True)
    raw = json.loads(proc.stdout)
    assert raw == sanitize(raw)
    assert raw['route_kind'] == route
    assert raw['canonical_kind'] == kind
    assert raw['has_canonical'] == (canonical is not None)
    assert [raw[k] for k in COUNTS] == [6, 1, 1, 1, 1, 5]
    for secret in ('987654', '876543', 'SECRET', 'PRIVATE', 'https:', '?private'):
        assert secret not in proc.stdout


@pytest.mark.parametrize('diagnostic', [unsafe(), RuntimeError('SECRET')])
def test_capture_failure_and_reset(diagnostic):
    adapter = TypeSearchAdapter()
    adapter.page = Mock(url='https://type.jp/job-1/')
    snapshot = {'ready': 'complete', 'links': []}
    adapter.page.evaluate.side_effect = [snapshot] * 5 + [diagnostic]
    with pytest.raises(GreenSearchDOMPending) as exc:
        adapter.search_cards('IT・Webエンジニア', 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'
    assert adapter.page.evaluate.call_args.args == (SOURCE_STRUCTURE,)
    assert adapter.last_safe_source_diagnostic == (None if isinstance(diagnostic, Exception) else sanitize(diagnostic))
    adapter.page.evaluate.side_effect = [{'ready': 'complete', 'links': ['/job-987654/876543_detail/']}]
    assert adapter.search_cards('IT・Webエンジニア', 1)
    assert adapter.last_safe_source_diagnostic is None


@pytest.mark.parametrize('platform', ['type', 'green', 'forkwell', 'lapras', 'findy'])
@pytest.mark.parametrize('reason', ['NO_VALID_JOB_LINKS', 'PARSE_ERROR', 'NEEDS_LOGIN'])
@pytest.mark.parametrize('injected', [True, False])
def test_cli_gating(monkeypatch, capsys, platform, reason, injected):
    import scout_agent.search as search
    def fail(args, settings, *, adapter, progress):
        adapter.last_safe_source_diagnostic = unsafe()
        raise GreenSearchDOMPending('JOB_LINKS', reason)
    monkeypatch.setattr(search, '_search_command', fail)
    assert search_command(SimpleNamespace(platform=platform), None,
                          adapter=TypeSearchAdapter() if injected else None) == 1
    out = capsys.readouterr()
    prefix = 'Type safe source diagnostic: '
    lines = [s for s in out.err.splitlines() if s.startswith(prefix)]
    expected = platform == 'type' and reason == 'NO_VALID_JOB_LINKS'
    assert len(lines) == int(expected)
    assert prefix not in out.out
    assert 'SECRET' not in out.err
    if expected:
        assert json.loads(lines[0][len(prefix):]) == sanitize(unsafe())
        assert out.err.splitlines()[-1] == 'Type safety stop: source=NONE page=0 category=NO_VALID_JOB_LINKS'


def test_web_ignores_diagnostic():
    manager = SearchRunManager()
    before = manager.snapshot()
    manager._emit('Type safe source diagnostic: ' + json.dumps(unsafe()))
    assert manager.snapshot() == before


@pytest.mark.parametrize('snapshot,reason', [({'login': True}, 'NEEDS_LOGIN'),
    ({'ready': 'loading'}, 'PARSE_ERROR'), ({'links': None}, 'PARSE_ERROR'),
    ({'links': ['/job-987654/876543_detail/']}, None)])
def test_capture_only_after_five_empty_snapshots(snapshot, reason):
    adapter = TypeSearchAdapter()
    adapter.last_safe_source_diagnostic = sanitize(unsafe())
    adapter.page = Mock(url='https://type.jp/job-1/')
    adapter.page.evaluate.return_value = {'ready': 'complete', 'links': [], **snapshot}
    if reason:
        with pytest.raises(GreenSearchDOMPending) as exc:
            adapter.search_cards('IT・Webエンジニア', 1)
        assert exc.value.reason == reason
    else:
        assert adapter.search_cards('IT・Webエンジニア', 1)
    assert adapter.last_safe_source_diagnostic is None
    assert adapter.page.evaluate.call_count == 1


def test_diagnostic_navigation_exception_keeps_primary_failure():
    adapter = TypeSearchAdapter()
    adapter.page = Mock(url='https://type.jp/job-1/')
    calls = 0
    def evaluate(script):
        nonlocal calls
        calls += 1
        if calls == 6:
            adapter.page.url = 'https://fictional.invalid/private'
            return unsafe()
        return {'ready': 'complete', 'links': []}
    adapter.page.evaluate.side_effect = evaluate
    with pytest.raises(GreenSearchDOMPending) as exc:
        adapter.search_cards('IT・Webエンジニア', 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'
    assert adapter.last_safe_source_diagnostic is None
