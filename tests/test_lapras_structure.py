"""Execute the diagnostic against fictional trees, with no browser access."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.lapras_structure import STRUCTURE, sanitize
from scout_agent.platform_discovery import collect, evidence, LAPRAS_DETAIL_SNAPSHOT, SNAPSHOT


def run_tree(tree, structure=STRUCTURE, path="/companies/900001/jobs/fictional-key"):
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
    result = subprocess.run([node, '-e', script + '\nglobalThis.location = {protocol: "https:", host: "findy-code.io", pathname: ' + json.dumps(path) + '};\nconsole.log(JSON.stringify((' + structure + ')()));'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'FICTIONAL_SECRET' not in result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize('tree,relation', [
    ("e('main', '', null, [e('h2', '仕事内容'), e('p', 'FICTIONAL_SECRET_BODY'), e('h2', '応募資格')])", 'same-parent-next-sibling'),
    ("e('article', '', null, [e('div', '', null, [e('div', '', null, [e('h2', '仕事内容')])]), e('section', 'FICTIONAL_SECRET_BODY', null, [e('h3', '概要')])])", 'ancestor-next-sibling'),
    ("e('main', '', null, [e('h2', '仕事内容'), e('div', '', null, [e('h3', '概要'), e('p', 'FICTIONAL_SECRET_BODY')])])", 'nested-next-block'),
    ("e('main', '', null, [e('button', '仕事内容', 'tab'), e('div', '', 'tabpanel', [e('h3', '概要'), e('p', 'FICTIONAL_SECRET_BODY')])])", 'tab-panel'),
])
def test_wrapper_ancestor_nested_and_tab_panel(tree, relation):
    raw = run_tree(tree)
    result = sanitize(raw)
    label = next(i for i in result['labels'] if i['label'] == '仕事内容')
    expected = 'same-parent-following-sibling' if relation == 'same-parent-next-sibling' else relation
    assert expected in label['next_fixed_label_relationship_candidates']
    assert label['next_fixed_label'] in {'概要', '応募資格'}
    assert len(label['ancestors']) <= 3
    assert result['has_document_title'] and result['has_og_title']
    assert result['responsibilities_prefix_elements'] == []
    if relation != 'nested-next-block':
        assert relation in label['content_relationship_candidates']
    assert raw['labels'][0]['label'] == '仕事内容'


def test_prefix_only_tag_and_boolean_and_unknown_role():
    result = sanitize(run_tree("e('main', '', null, [e('h1', 'FICTIONAL_SECRET_TITLE'), e('custom-secret', '仕事内容 FICTIONAL_SECRET_BODY', 'FICTIONAL_SECRET_ROLE')])"))
    assert result['visible_h1_count'] == 1
    assert result['labels'] == []
    assert result['responsibilities_prefix_elements'] == [
        {'tag': None, 'starts_with_responsibilities': True}]


def test_output_layer_rejects_arbitrary_fields_and_strings():
    result = sanitize({'visible_h1_count': 'FICTIONAL_SECRET', 'has_og_title': 'secret',
        'labels': [{'label': '仕事内容', 'tag': 'secret', 'role': 'secret', 'class': 'secret',
                    'semantic_role': 'secret', 'text': 'secret',
                    'ancestors': [{'tag': 'div', 'role': 'secret', 'id': 'secret'}] * 5,
                    'content_relationship_candidates': ['secret', 'ancestor-next-sibling'],
                    'next_fixed_label': 'secret', 'next_fixed_label_relationship_candidates': ['secret']},
                   {'label': 'secret'}],
        'responsibilities_prefix_elements': [{'tag': 'div', 'starts_with_responsibilities': True}]})
    assert 'secret' not in json.dumps(result)
    assert result['visible_h1_count'] == 0
    assert result['labels'][0]['tag'] is None
    assert len(result['labels'][0]['ancestors']) == 3
    assert result['responsibilities_prefix_elements'] == []


@pytest.mark.parametrize('platform,url,script', [
    ('lapras', 'https://lapras.com/jobs/123', LAPRAS_DETAIL_SNAPSHOT),
    ('lapras', 'https://lapras.com/jobs/fictional', SNAPSHOT),
    ('lapras', 'https://lapras.com/jobs/123/apply', SNAPSHOT),
    ('lapras', 'https://lapras.com/search', SNAPSHOT),
    ('forkwell', 'https://jobs.forkwell.com/jobs/123', SNAPSHOT),
])
def test_numeric_only_read_only_dispatch(platform, url, script):
    page = Mock(url=url)
    page.evaluate.return_value = {'ready': 'complete', 'lapras_structure': {}}
    row = collect(SimpleNamespace(contexts=[SimpleNamespace(pages=[page])]), [platform])[0]
    assert page.method_calls == [('evaluate', (script,), {})]
    assert ('lapras_structure' in row) == (script == LAPRAS_DETAIL_SNAPSHOT)


def test_login_discards_structure():
    row = evidence('lapras', 'https://lapras.com/jobs/123', {'login': True, 'lapras_structure': {'labels': ['secret']}})
    assert row == {'platform': 'lapras', 'safe_failure_category': 'NEEDS_LOGIN'}


@pytest.mark.parametrize('block,tag,count,heading,fixed', [
    ("e('p', 'FICTIONAL_SECRET_BODY')", 'p', '0', False, False),
    ("e('div', '', null, [e('p', 'FICTIONAL_SECRET_BODY')])", 'div', '1', False, False),
    ("e('div', '', null, [e('h4', 'FICTIONAL_SECRET_HEADING'), e('p', 'FICTIONAL_SECRET_BODY')])", 'div', '2-5', True, False),
    ("e('div', '', null, [e('h4', '必須要件'), e('p', 'FICTIONAL_SECRET_BODY')])", 'div', '2-5', True, True),
    ("e('div', '', null, [e('section', '', null, [e('div', '', null, [e('p', 'FICTIONAL_SECRET_BODY')])])])", 'div', '1', False, False),
    ("e('div', '', 'hidden', [e('p', 'FICTIONAL_SECRET_BODY', 'hidden')])", 'div', '1', False, False),
])
def test_summary_content_shapes(block, tag, count, heading, fixed):
    result = sanitize(run_tree("e('main', '', null, [e('h2', '仕事内容'), e('h3', '概要'), " + block + "] )"))
    assert result['summary_node']['tag'] == 'h3'
    assert result['summary_parent']['tag'] == 'main'
    sibling = result['immediate_next_sibling']
    assert sibling['tag'] == tag
    assert sibling['child_count_bucket'] == count
    assert sibling['contains_any_heading'] is heading
    assert sibling['contains_fixed_label'] is fixed
    assert sibling['has_direct_nonempty_text'] is (tag == 'p')
    assert sibling['has_descendant_nonempty_text'] is (tag == 'div')
    assert sibling['visible'] is ('hidden' not in block)
    assert len(result['immediate_next_sibling_children']) == (0 if tag == 'p' else (2 if count == '2-5' else 1))
    assert all(i['visible'] for i in result['first_descendant_shapes'])
    if 'hidden' in block:
        assert result['first_descendant_shapes'] == []
    if heading:
        assert result['first_descendant_shapes'][0]['is_heading']
        assert result['first_descendant_shapes'][0]['fixed_label'] == ('必須要件' if fixed else None)


def test_summary_without_sibling_and_exact_label_gate():
    result = sanitize(run_tree("e('main', '', null, [e('h2', '業務内容'), e('h3', '概要')])"))
    assert result['immediate_next_sibling'] is None
    assert result['immediate_next_sibling_children'] == []
    assert result['first_descendant_shapes'] == []
    for label in ('FICTIONAL_SECRET', '仕事内容 FICTIONAL_SECRET'):
        result = sanitize(run_tree("e('main', '', null, [e('h2', '" + label + "'), e('h3', '概要'), e('p', 'FICTIONAL_SECRET')])"))
        assert 'summary_node' not in result


def test_summary_sanitizer_rebuilds_all_fields_and_caps_arrays():
    secret = {'tag': 'FICTIONAL_SECRET', 'role': 'FICTIONAL_SECRET',
              'semantic_role': 'FICTIONAL_SECRET', 'class': 'FICTIONAL_SECRET',
              'id': 'FICTIONAL_SECRET', 'data-secret': 'FICTIONAL_SECRET',
              'text': 'FICTIONAL_SECRET', 'heading_text': 'FICTIONAL_SECRET',
              'fixed_label': 'FICTIONAL_SECRET', 'visible': 'FICTIONAL_SECRET',
              'is_heading': True, 'has_direct_nonempty_text': 'FICTIONAL_SECRET',
              'has_descendant_nonempty_text': True, 'child_count_bucket': 'FICTIONAL_SECRET'}
    result = sanitize({'labels': [{'label': '職務内容'}, {'label': '概要'}],
                       'summary_node': secret, 'summary_parent': secret,
                       'immediate_next_sibling': secret,
                       'immediate_next_sibling_children': [secret] * 20,
                       'first_descendant_shapes': [secret] * 20})
    assert 'FICTIONAL_SECRET' not in json.dumps(result)
    assert len(result['immediate_next_sibling_children']) == 8
    assert len(result['first_descendant_shapes']) == 12
    assert result['first_descendant_shapes'][0]['is_heading'] is True
    assert result['first_descendant_shapes'][0]['visible'] is False


def test_probe_dfs_order_and_limits():
    nested = "e('section', '', null, [e('span', '', null, [e('p', 'FICTIONAL_SECRET')])])"
    children = [nested] + ["e('p', 'FICTIONAL_SECRET')"] * 15
    raw = run_tree("e('main', '', null, [e('h2', '職務内容'), e('h3', '概要'), e('div', '', null, [" + ','.join(children) + "])])")
    assert raw['immediate_next_sibling']['child_count_bucket'] == '6+'
    assert len(raw['immediate_next_sibling_children']) == 8
    assert len(raw['first_descendant_shapes']) == 12
    assert [i['tag'] for i in raw['first_descendant_shapes'][:3]] == ['section', 'span', 'p']
    assert sanitize(raw)['first_descendant_shapes'] == raw['first_descendant_shapes']
