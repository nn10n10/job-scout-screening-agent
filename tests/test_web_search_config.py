"""Execute the shipped JavaScript with a fictional form/storage, without a browser."""
import json
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from scout_agent.green_discovery import KEYWORDS
from scout_agent.web.app import create_app
from scout_agent.web.search_runs import LIMITS


def test_search_config_reload_and_validation(tmp_path):
    node = shutil.which('node')
    assert node, 'Node is required for the Search form regression test'
    script = Path('scout_agent/web/static/search_config.js').read_text()
    script += '\nconst limits = ' + json.dumps(LIMITS) + ';\n'
    script += 'const labels = ' + json.dumps(list(KEYWORDS)) + ';\n'
    script += r'''
const assert = require('node:assert/strict');
let stored = null;
global.sessionStorage = {
  getItem: () => stored,
  setItem: (key, value) => { stored = value; }
};
function newForm() {
  const checkboxes = labels.map(value => ({value, checked:true}));
  const numbers = Object.entries(limits).map(([name, [value]]) => ({name, value:String(value)}));
  return {checkboxes, numbers, querySelectorAll(selector) {
    if (selector === '[name=sources]') return checkboxes;
    if (selector === '[name=sources]:checked') return checkboxes.filter(input => input.checked);
    if (selector === '[type=number]') return numbers;
    throw Error('Unexpected selector');
  }};
}
const first = newForm();
first.csrf = 'fictional-token-not-to-be-stored';
first.checkboxes.forEach(input => input.checked = input.value === 'AWS');
first.numbers.find(input => input.name === 'coverage_pages').value = '1';
for (const name of ['max_jobs', 'max_model_jobs']) first.numbers.find(input => input.name === name).value = '25';
assert.equal(SearchConfig.save(first, limits), true);
const saved = JSON.parse(stored);
assert.deepEqual(saved.sources, ['AWS']);
assert.equal(saved.coverage_pages, 1);
assert.equal(stored.includes('csrf'), false);
assert.equal(stored.includes(first.csrf), false);
// Simulate the page reload after completion, then submit a second time.
const second = newForm();
SearchConfig.restore(second, limits);
assert.deepEqual(second.checkboxes.filter(input => input.checked).map(input => input.value), ['AWS']);
assert.equal(second.numbers.find(input => input.name === 'coverage_pages').value, 1);
assert.equal(SearchConfig.save(second, limits), true);
assert.deepEqual(JSON.parse(stored), saved);
const invalid = [null, [], {}, {...saved, sources:[]}, {...saved, sources:['unknown']},
  {...saved, sources:['AWS','AWS']}, {...saved, csrf:'secret'}, {...saved, coverage_pages:'1'},
  {...saved, coverage_pages:true}, {...saved, coverage_pages:1.5}];
for (const [name, [, low, high]] of Object.entries(limits)) {
  invalid.push({...saved, [name]:low-1}, {...saved, [name]:high+1});
}
for (const value of [...invalid.map(value => JSON.stringify(value)), '{bad json']) {
  stored = value;
  const fallback = newForm();
  SearchConfig.restore(fallback, limits);
  assert.ok(fallback.checkboxes.every(input => input.checked));
  for (const input of fallback.numbers) assert.equal(input.value, String(limits[input.name][0]));
}
global.sessionStorage = {getItem() {throw Error('disabled');}, setItem() {throw Error('disabled');}};
SearchConfig.restore(newForm(), limits);
assert.equal(SearchConfig.save(first, limits), true);
console.log('AWS-only configuration survives reload and second submit; invalid storage uses defaults; no CSRF stored');
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, shell=False)
    assert result.returncode == 0, result.stderr
    assert 'survives reload' in result.stdout


def test_search_page_wires_persistence_before_submit(tmp_path):
    app = create_app(tmp_path / 'fictional.db', search_runner=lambda *args: 0)
    with TestClient(app) as client:
        html = client.get('/search').text
        assert '/static/search_config.js' in html
        assert 'SearchConfig.restore(form, configLimits)' in html
        assert html.index('SearchConfig.save(form, configLimits)') < html.index("fetch('/search/run',")
        assert "location.reload()" in html
        assert client.get('/static/search_config.js').status_code == 200
        assert app.state.search_runs.snapshot()['status'] == 'idle'
