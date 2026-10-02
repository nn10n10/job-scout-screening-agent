"""Fictional DOM and mocked integration; no real browser/site/model access."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.type_detail_structure import STRUCTURE, sanitize
from scout_agent.type_search import TypeSearchAdapter, job_from_url
from scout_agent.green_discovery import GreenSearchDOMPending
from scout_agent.search import search_command
from scout_agent.web.search_runs import SearchRunManager

def run_tree(tree, structure=STRUCTURE, path="/job-900001/800002_detail/"):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for fictional DOM verification')
    script = r'''
class Element {
 constructor(tag, text = '', role = null, children = []) {
  this.tagName = tag.toUpperCase(); this.ownText = text; this.role = role;
  this.children = children; children.forEach(c => c.parentElement = this);
 }
 get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
 getAttribute(name) { return name === 'role' ? this.role : 'FICTIONAL_SECRET_ATTRIBUTE'; }
 getClientRects() { return this.role === 'hidden' ? [] : [1]; }
 get childNodes() { return [{nodeType: 3, textContent: this.ownText}, ...this.children]; }
 querySelectorAll() { const out = []; const visit = e => { out.push(e); e.children.forEach(visit); }; this.children.forEach(visit); return out; }
 get nextElementSibling() {
  const siblings = this.parentElement?.children || [];
  return siblings[siblings.indexOf(this) + 1] || null;
 }
 contains(e) { return this === e || this.children.some(c => c.contains(e)); }
}
const e = (...args) => new Element(...args);
const root = TREE;
const all = [];
const walk = e => { all.push(e); e.children.forEach(walk); }; walk(root);
globalThis.getComputedStyle = () => ({visibility: 'visible'});
globalThis.document = {title: 'FICTIONAL_SECRET_TITLE',
 querySelectorAll: () => all,
 querySelector: s => s === 'meta[property="og:title"]' ? {content: 'FICTIONAL_SECRET_OG'} : null};
'''.replace('TREE', tree)
    result = subprocess.run([node, '-e', script + '\nglobalThis.location = {protocol: "https:", host: "type.jp", pathname: ' + json.dumps(path) + '};\nconsole.log(JSON.stringify((' + structure + ')()));'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'FICTIONAL_SECRET' not in result.stdout
    return json.loads(result.stdout)


URL = 'https://type.jp/job-900001/800002_detail/'


def unsafe():
    secret = 'FICTIONAL_SECRET https://type.jp/job-900001/800002_detail/'
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
    for value in ('FICTIONAL_SECRET', '900001', '800002', 'https:', 'class', 'data-private', 'raw_text', 'summary_node', '"id"'):
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


@pytest.mark.parametrize('path', ['/job-900001/', '/job-900001/800002_detail/apply', '/job-x/800002_detail/', '/job-900001/800002_detail'])
def test_js_exact_route_guard(path):
    assert run_tree("e('h2', '仕事内容')", STRUCTURE, path) == {}


@pytest.mark.parametrize('title,reason', [('Fictional title', 'RESPONSIBILITIES_MISSING'), ('', 'TITLE_MISSING')])
@pytest.mark.parametrize('diagnostic', [unsafe(), RuntimeError('FICTIONAL_SECRET')])
def test_capture_preserves_failure_and_resets(title, reason, diagnostic):
    a = TypeSearchAdapter()
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
    ('type', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), True),
    ('type', GreenSearchDOMPending('DETAIL_TITLE', 'TITLE_MISSING'), True),
    ('type', GreenSearchDOMPending('JOB_LINKS', 'NO_VALID_JOB_LINKS'), False),
    ('type', ValueError('FICTIONAL_SECRET'), False),
    ('green', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), False),
    ('forkwell', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), False),
    ('lapras', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'), False),
    ('findy', GreenSearchDOMPending('DETAIL_TITLE', 'TITLE_MISSING'), False),
    ('type', GreenSearchDOMPending('DETAIL_RESPONSIBILITIES', 'PARSE_ERROR'), False),
    ('type', GreenSearchDOMPending('DETAIL_NAVIGATION', 'JOB_URL_MISMATCH'), False),
])
@pytest.mark.parametrize('injected', [True, False])
def test_cli_gating(monkeypatch, capsys, platform, exception, expected, injected):
    import scout_agent.search as search
    def fail(args, settings, *, adapter, progress):
        adapter.last_safe_detail_diagnostic = unsafe()
        raise exception
    monkeypatch.setattr(search, '_search_command', fail)
    assert search_command(SimpleNamespace(platform=platform), None,
                          adapter=TypeSearchAdapter() if injected else None) == 1
    output = capsys.readouterr()
    prefix = 'Type safe detail diagnostic: '
    lines = [s for s in output.err.splitlines() if s.startswith(prefix)]
    assert len(lines) == int(expected)
    assert prefix not in output.out
    assert 'FICTIONAL_SECRET' not in output.err
    if expected:
        assert json.loads(lines[0][len(prefix):]) == sanitize(unsafe())
        assert output.err.splitlines()[-1].startswith('Type safety stop: ')


def test_web_ignores_diagnostic():
    manager = SearchRunManager()
    before = manager.snapshot()
    manager._emit('Type safe source diagnostic: FICTIONAL_SECRET')
    manager._emit('Type safe detail diagnostic: ' + json.dumps(sanitize(unsafe())))
    assert manager.snapshot() == before


@pytest.mark.parametrize('key,values', [
    ('label', ('仕事内容', '応募資格', '必須要件', '必須条件', '歓迎要件', '歓迎条件', '給与', '年収', '勤務地', '開発環境', '技術', '募集要項', '勤務時間', '福利厚生', '雇用形態')),
    ('tag', ('h2', 'dt', 'span', 'section')),
    ('role', ('heading', 'tab', 'tabpanel', 'region')),
    ('semantic_role', ('main', 'article', 'section', 'heading')),
])
def test_fixed_descriptor_allowlists(key, values):
    for value in values:
        record = {'label': '仕事内容', key: value}
        assert sanitize({'labels': [record]})['labels'][0][key] == value
    record = {'label': '仕事内容', key: 'PRIVATE 987654 https://fictional.invalid'}
    result = sanitize({'labels': [record]})['labels']
    if key == 'label':
        assert result == []
    else:
        assert result[0][key] is None


def test_all_relations_and_absent_shapes():
    from scout_agent.type_detail_structure import RELATIONS
    result = sanitize({'labels': [{'label': '仕事内容',
        'content_relationship_candidates': [*RELATIONS, 'PRIVATE'],
        'next_fixed_label_relationship_candidates': [*RELATIONS, 'PRIVATE']}],
        'immediate_next_sibling': None})
    assert set(result['labels'][0]['content_relationship_candidates']) == RELATIONS
    assert set(result['labels'][0]['next_fixed_label_relationship_candidates']) == RELATIONS
    assert result['immediate_next_sibling'] is None
    assert result['first_descendant'] is None
    assert sanitize({'visible_h1_count': 987654})['visible_h1_count'] == 100
    for count in (True, -1, '987654', None):
        assert sanitize({'visible_h1_count': count})['visible_h1_count'] == 0
    raw = run_tree("e('main', '', null, [e('h1', 'FICTIONAL_SECRET'), e('h2', '給与')])", STRUCTURE)
    assert raw['visible_h1_count'] == 1
    assert 'immediate_next_sibling' not in raw
    assert 'first_descendant' not in raw


@pytest.mark.parametrize('title,reason', [('Fictional', 'RESPONSIBILITIES_MISSING'), ('', 'TITLE_MISSING')])
def test_capture_navigation_exception_keeps_primary(title, reason):
    a = TypeSearchAdapter()
    a.page = Mock(url=URL)
    snapshot = {'ready': 'complete', 'canonical': URL}
    responses = iter([snapshot, {'title': title, 'sections': {}}, snapshot] * 5)
    def evaluate(script):
        if script == STRUCTURE:
            a.page.url = 'https://fictional.invalid/private'
            return unsafe()
        return next(responses)
    a.page.evaluate.side_effect = evaluate
    with pytest.raises(GreenSearchDOMPending) as exc:
        a.job_detail(job_from_url(URL, {}))
    assert exc.value.reason == reason
    assert a.last_safe_detail_diagnostic is None


@pytest.mark.parametrize('diagnostic', [None, 'FICTIONAL_SECRET'])
def test_cli_absent_or_invalid_diagnostic(monkeypatch, capsys, diagnostic):
    import scout_agent.search as search
    def fail(args, settings, *, adapter, progress):
        adapter.last_safe_detail_diagnostic = diagnostic
        raise GreenSearchDOMPending('DETAIL_TITLE', 'TITLE_MISSING')
    monkeypatch.setattr(search, '_search_command', fail)
    assert search_command(SimpleNamespace(platform='type'), None) == 1
    output = capsys.readouterr()
    assert 'safe detail diagnostic:' not in output.err
    assert 'FICTIONAL_SECRET' not in output.err
    assert output.err.startswith('Type safety stop: ')
