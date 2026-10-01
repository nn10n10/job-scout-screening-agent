"""Execute the diagnostic against fictional trees, with no browser access."""
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scout_agent.lapras_structure import STRUCTURE, sanitize
from scout_agent.platform_discovery import collect, evidence, LAPRAS_DETAIL_SNAPSHOT, SNAPSHOT


def run_tree(tree):
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
 getClientRects() { return [1]; }
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
    result = subprocess.run([node, '-e', script + '\nconsole.log(JSON.stringify((' + STRUCTURE + ')()));'],
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
