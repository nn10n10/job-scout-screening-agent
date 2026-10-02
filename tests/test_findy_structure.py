"""Fictional DOM and mocked integration; no real browser/site/model access."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.findy_structure import STRUCTURE, sanitize
from scout_agent.findy_search import FindySearchAdapter, job_from_url
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.platform_discovery import collect, evidence, FINDY_DETAIL_SNAPSHOT, SNAPSHOT
from scout_agent.search import search_command
from scout_agent.web.search_runs import SearchRunManager
from test_lapras_structure import run_tree

URL = 'https://findy-code.io/companies/900001/jobs/fictional-key'


def unsafe():
    secret = 'FICTIONAL_SECRET https://findy-code.io/companies/900001/jobs/fictional-key'
    descriptor = {'tag': 'div', 'role': ['secret'], 'semantic_role': {},
                  'text': secret, 'class': secret, 'id': secret, 'data-private': secret,
                  'fixed_label': secret, 'visible': secret, 'is_heading': True,
                  'has_direct_nonempty_text': True, 'has_descendant_nonempty_text': True}
    return {'visible_h1_count': secret, 'has_og_title': True, 'has_document_title': secret,
            'labels': [{'label': '仕事内容', **descriptor, 'ancestors': [descriptor] * 5,
                        'content_relationship_candidates': ['same-parent-next-sibling', secret],
                        'next_fixed_label': '給与'}, {'label': secret}],
            'immediate_next_sibling': descriptor, 'first_descendant': descriptor,
            'raw_text': secret, 'url': secret, 'summary_node': descriptor}


def test_rebuild_schema_and_shape():
    result = sanitize(unsafe())
    encoded = json.dumps(result)
    for value in ('FICTIONAL_SECRET', '900001', 'fictional-key', 'https:', 'class', 'data-private', 'raw_text', 'summary_node', '"id"'):
        assert value not in encoded
    assert len(result['labels'][0]['ancestors']) == 3
    assert result['immediate_next_sibling'] == dict(tag='div', is_heading=True, visible=False,
        has_direct_nonempty_text=True, has_descendant_nonempty_text=True, fixed_label=None)
    assert sanitize(result) == result
    assert sanitize({'labels': None})['labels'] == []
    assert 'immediate_next_sibling' not in sanitize({'immediate_next_sibling': unsafe()})


@pytest.mark.parametrize('tree,relation', [
    ("e('main', '', null, [e('h2', '仕事内容'), e('p', 'FICTIONAL_SECRET'), e('h3', '給与')])", 'same-parent-next-sibling'),
    ("e('main', '', null, [e('div', '', null, [e('span', '仕事内容')]), e('section', '', null, [e('p', 'FICTIONAL_SECRET'), e('h3', '給与')])])", 'ancestor-next-sibling'),
    ("e('main', '', null, [e('button', '仕事内容', 'tab'), e('div', '', 'tabpanel', [e('h3', '給与'), e('p', 'FICTIONAL_SECRET')])])", 'tab-panel'),
])
def test_fictional_dom_relations(tree, relation):
    raw = run_tree(tree, STRUCTURE)
    result = sanitize(raw)
    assert relation in result['labels'][0]['content_relationship_candidates']
    assert result['labels'][0]['next_fixed_label'] == '給与'
    if relation == 'ancestor-next-sibling':
        assert result['immediate_next_sibling'] is None
    else:
        assert result['immediate_next_sibling']['tag'] in {'p', 'div'}
    assert '概要' not in json.dumps(raw)


def test_shape_first_descendant_and_hidden():
    tree = "e('main', '', null, [e('h2', '仕事内容'), e('div', '', 'hidden', [e('p', 'FICTIONAL_SECRET', 'hidden')])])"
    result = sanitize(run_tree(tree, STRUCTURE))
    assert result['first_descendant']['tag'] == 'p'
    assert not result['first_descendant']['visible']
    assert result['first_descendant']['has_direct_nonempty_text']
    assert result['immediate_next_sibling']['has_descendant_nonempty_text']


@pytest.mark.parametrize('path', ['/recommends', '/companies/900001/jobs/fictional-key/apply', '/companies/x/jobs/key', '/companies/900001/jobs/key/'])
def test_js_exact_route_guard(path):
    assert run_tree("e('h2', '仕事内容')", STRUCTURE, path) == {}


@pytest.mark.parametrize('url,expected', [(URL, True), (URL + '/apply', False),
    (URL + '/', False), ('https://findy-code.io/recommends', False),
    ('https://findy-code.io/companies/fictional/jobs/key', False)])
def test_discovery_only_existing_exact_detail(url, expected):
    page = Mock(url=url)
    page.evaluate.return_value = {'ready': 'complete', 'findy_structure': unsafe()}
    row = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), ['findy'])[0]
    assert page.method_calls == [('evaluate', (FINDY_DETAIL_SNAPSHOT if expected else SNAPSHOT,), {})]
    assert ('findy_structure' in row) == expected
    assert 'FICTIONAL_SECRET' not in json.dumps(row)


def test_discovery_login_discards_diagnostic():
    assert evidence('findy', URL, {'login': True, 'findy_structure': unsafe()}) == {
        'platform': 'findy', 'safe_failure_category': 'NEEDS_LOGIN'}


@pytest.mark.parametrize('title,reason', [('Fictional title', 'RESPONSIBILITIES_MISSING'), ('', 'TITLE_MISSING')])
@pytest.mark.parametrize('diagnostic', [unsafe(), RuntimeError('FICTIONAL_SECRET')])
def test_capture_preserves_failure_and_resets(title, reason, diagnostic):
    a = FindySearchAdapter()
    a.page = Mock(url=URL)
    snapshot = {'ready': 'complete', 'canonical': URL}
    a.page.evaluate.side_effect = [snapshot, {'title': title, 'sections': {}}, snapshot] + [snapshot, {'title': title, 'sections': {}}, snapshot] * 4 + [diagnostic]
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job_from_url(URL, {}))
    assert exc.value.reason == reason
    assert a.page.evaluate.call_args.args == (STRUCTURE,)
    assert a.last_safe_detail_diagnostic == (None if isinstance(diagnostic, Exception) else sanitize(diagnostic))
    a.page.evaluate.side_effect = [snapshot, {'title': 'Fictional', 'sections': {'仕事内容': '架空運用'}}, snapshot]
    assert a.job_detail(job_from_url(URL, {}))['responsibilities'] == '架空運用'
    assert a.last_safe_detail_diagnostic is None


@pytest.mark.parametrize('platform,exception,expected', [
    ('findy', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), True),
    ('findy', GreenSearchDOMPending('DETAIL_TITLE', 'TITLE_MISSING'), True),
    ('findy', GreenSearchDOMPending('JOB_LINKS', 'NO_VALID_JOB_LINKS'), False),
    ('findy', ValueError('FICTIONAL_SECRET'), False),
    ('green', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), False),
    ('forkwell', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), False),
])
@pytest.mark.parametrize('injected', [True, False])
def test_cli_gating(monkeypatch, capsys, platform, exception, expected, injected):
    import scout_agent.search as search
    def fail(args, settings, *, adapter, progress):
        adapter.last_safe_detail_diagnostic = unsafe()
        raise exception
    monkeypatch.setattr(search, '_search_command', fail)
    assert search_command(SimpleNamespace(platform=platform), None,
                          adapter=FindySearchAdapter() if injected else None) == 1
    output = capsys.readouterr()
    prefix = 'Findy safe detail diagnostic: '
    lines = [s for s in output.err.splitlines() if s.startswith(prefix)]
    assert len(lines) == int(expected)
    assert prefix not in output.out
    assert 'FICTIONAL_SECRET' not in output.err
    if expected:
        assert json.loads(lines[0][len(prefix):]) == sanitize(unsafe())


def test_web_ignores_diagnostic():
    manager = SearchRunManager()
    before = manager.snapshot()
    manager._emit('Findy safe detail diagnostic: ' + json.dumps(sanitize(unsafe())))
    assert manager.snapshot() == before
