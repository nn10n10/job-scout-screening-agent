"""TASK-016: fictional offline sources, configuration and budget acceptance."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from js_test_utils import require_node
from test_indeed_search import adapter, JD, ID
from scout_agent.indeed_search import (IndeedSearchAdapter, SOURCES, SOURCE_PRESETS,
    GENERAL_DEV_SOURCES, INFRA_SOURCES, prioritize_sources, job_from_url)
from scout_agent.search import run_search, SearchStore, SearchEvaluation, POLICY_VERSION
from scout_agent.storage.db import Database
from scout_agent.web.search_runs import validate_config, SearchRunManager, SEARCH_SOURCES


@pytest.mark.parametrize('source', GENERAL_DEV_SOURCES)
def test_new_source_shape_first_page_identity_and_fail_closed(source):
    a = adapter()
    url = a.source_url(source)
    assert urlsplit(url).path == '/jobs'
    assert parse_qs(urlsplit(url).query) == {'q': [source]}
    jobs = a.search_cards(source, 1)
    assert [(j.job_id, j.url) for j in jobs] == [('indeed:' + ID, JD)]
    for page in (2, 0, True):
        with pytest.raises(ValueError): a.source_url(source, page)
    from scout_agent.green_discovery import GreenSearchDOMPending
    with pytest.raises(GreenSearchDOMPending) as exc:
        adapter({'challenge': True}).search_cards(source, 1)
    assert exc.value.reason == 'CHALLENGE'
    with pytest.raises(GreenSearchDOMPending):
        adapter(landing=url + '&start=10').search_cards(source, 1)
    manager = SearchRunManager()
    manager._emit(f'Search progress: {source} page 1')
    assert source in manager.snapshot()['summary'][0]
    manager._emit(f'Indeed safety stop: source={source} page=1 category=CHALLENGE')
    assert manager.snapshot()['failed_source'] == source


@pytest.mark.parametrize('preset', SOURCE_PRESETS)
def test_cli_presets_and_keywords(monkeypatch, preset):
    from scout_agent import cli, search
    monkeypatch.setattr(cli, 'load_settings', lambda: None)
    command = Mock(return_value=0)
    monkeypatch.setattr(search, 'search_command', command)
    assert cli.main(['search', 'indeed', '--source-preset', preset]) == 0
    assert command.call_args.args[0].keyword == list(SOURCE_PRESETS[preset])
    command.reset_mock()
    assert cli.main(['search', 'green', '--source-preset', preset]) == 1
    assert cli.main(['search', 'indeed', '--source-preset', preset, '--keyword', 'SRE']) == 1
    command.assert_not_called()
    for source in GENERAL_DEV_SOURCES:
        assert cli.main(['search', 'indeed', '--keyword', source]) == 0
        assert command.call_args.args[0].keyword == [source]


@pytest.mark.parametrize('incremental', [True, False])
@pytest.mark.parametrize('cached', [True, False])
def test_mixed_small_budget_development_first_preserves_cache_state(tmp_path, incremental, cached):
    a = IndeedSearchAdapter()
    sources = list(INFRA_SOURCES) + list(GENERAL_DEV_SOURCES)
    def cards(source, page):
        assert page == 1
        suffix = SOURCES.index(source)
        return [job_from_url('https://jp.indeed.com/viewjob?jk=fictional_job' + str(suffix), {})]
    a.search_cards = Mock(side_effect=cards)
    a.job_detail = Mock(return_value={'title': '架空 Backend', 'responsibilities': '自社オフィスで AWS Lambda API を開発。', 'salary': '600万円'})
    model = Mock(model_name='fictional-mock')
    model.classify_jobs.side_effect = lambda jobs: {j.job_id: SearchEvaluation(verdict='POSSIBLE', summary='架空判断') for j in jobs}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        first = cards(GENERAL_DEV_SOURCES[0], 1)[0]
        if cached:
            first.fields = dict(a.job_detail.return_value)
            store.save_job(first)
            store.save_result(first, POLICY_VERSION, SearchEvaluation(verdict='TARGET', summary='架空缓存'), 'codex', 'fictional-mock')
            store.set_user_state(first.job_id, 'EXCLUDED')
        _, stats = run_search(a, store, model, keywords=sources, max_jobs=1,
            max_model_jobs=1, early_stop=1, batch_size=1,
            pages_per_keyword=None if incremental else 2)
        visited = [call.args[0] for call in a.search_cards.call_args_list]
        assert visited[0] == GENERAL_DEV_SOURCES[0]
        assert stats['details'] <= 1 and stats['model_jobs'] <= 1
        if cached:
            assert stats['cache_hits'] == 1
            assert store.user_states()[first.job_id] == 'EXCLUDED'
            assert store.cached(first, POLICY_VERSION).summary == '架空缓存'
            if incremental:
                assert GENERAL_DEV_SOURCES[1] in visited
                assert stats['model_jobs'] == 1
        else:
            assert stats['model_jobs'] == 1
        assert all(call.args[1] == 1 for call in a.search_cards.call_args_list)


def test_development_only_visits_only_selected_sources(tmp_path):
    a = IndeedSearchAdapter()
    a.search_cards = Mock(return_value=[])
    model = Mock()
    with Database(tmp_path / 'fictional.db') as db:
        run_search(a, SearchStore(db), model, keywords=SOURCE_PRESETS['development'])
    assert [c.args for c in a.search_cards.call_args_list] == [(s, 1) for s in SOURCE_PRESETS['development']]
    model.classify_jobs.assert_not_called()
    assert prioritize_sources(INFRA_SOURCES) == INFRA_SOURCES
    assert prioritize_sources(GENERAL_DEV_SOURCES) == GENERAL_DEV_SOURCES
    with pytest.raises(ValueError): prioritize_sources(['求人一覧'])


def test_platform_whitelists_and_ui_presets(tmp_path):
    from fastapi.testclient import TestClient
    from scout_agent.web.app import create_app
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        response = client.get('/search')
    assert response.context['source_presets']['indeed'] == SOURCE_PRESETS
    assert tuple(response.context['source_labels']['indeed']) == SOURCES
    assert "preset.type = 'button'" in response.text
    assert 'values.includes(input.value)' in response.text
    for platform in SEARCH_SOURCES:
        assert validate_config({'platform': platform})['sources'] == list(SEARCH_SOURCES[platform])
        if platform != 'indeed':
            with pytest.raises(ValueError):
                validate_config({'platform': platform, 'sources': list(GENERAL_DEV_SOURCES)})
    for preset in SOURCE_PRESETS.values():
        assert validate_config({'platform': 'indeed', 'sources': list(preset)})['sources'] == list(preset)


def test_development_session_restore_and_cross_platform_rejection():
    config = validate_config({'platform': 'indeed', 'sources': list(SOURCE_PRESETS['development'])})
    limits = __import__('scout_agent.web.search_runs', fromlist=['LIMITS']).LIMITS
    js = Path('scout_agent/web/static/search_config.js').read_text()
    script = js + '\n' + '''
const assert = require('assert');
const data = CONFIG, limits = LIMITS, allowed = SOURCES;
let stored = JSON.stringify(data), inputs = [];
global.sessionStorage = {getItem: () => stored, setItem: (_, value) => {stored = value;}};
const numbers = Object.keys(limits).map(name => ({name, value: 0}));
const form = {elements: {platform: {value: 'green'}}, querySelectorAll: selector =>
  selector === '[type=number]' ? numbers : selector.includes(':checked') ? inputs.filter(i => i.checked) : inputs};
const render = () => { inputs = allowed.map(value => ({value, checked: false})); };
SearchConfig.restore(form, limits, render, {indeed: allowed});
assert.equal(form.elements.platform.value, 'indeed');
assert.deepEqual(inputs.filter(i => i.checked).map(i => i.value).sort(), [...data.sources].sort());
assert(SearchConfig.save(form, limits));
assert.deepEqual(JSON.parse(stored).sources.sort(), [...data.sources].sort());
for (const name of Object.keys(limits)) assert.equal(JSON.parse(stored)[name], data[name]);
assert.equal(SearchConfig.validate(data, ['インフラエンジニア'], limits), null);
console.log('development-only restore/save and platform whitelist passed');
'''.replace('CONFIG', json.dumps(config)).replace('LIMITS', json.dumps(limits)).replace('SOURCES', json.dumps(SOURCES))
    result = subprocess.run([require_node(), '-e', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_ui_preset_clicks_and_backend_argv(tmp_path):
    """Run actual template rendering JS against a fictional DOM, without a browser."""
    from fastapi.testclient import TestClient
    from scout_agent.web.app import create_app
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        response = client.get('/search')
    js = response.text.split('const configLimits = ', 1)[1].split("form.elements.platform.addEventListener", 1)[0]
    script = '''
const assert = require('assert');
class Element {
  constructor() { this.children = []; this.events = {}; }
  replaceChildren() { this.children = []; }
  append(...children) { this.children.push(...children); }
  querySelectorAll() { return this.children.flatMap(label => label.children).filter(e => e.name === 'sources'); }
  addEventListener(name, handler) { this.events[name] = handler; }
  closest() { return this; }
}
const container = new Element(), presets = new Element(), note = new Element();
const form = {elements: {platform: {value: 'indeed'}, coverage_pages: new Element(), max_depth: new Element()},
  querySelector: selector => selector === '.search-source-options' ? container : presets};
global.document = {getElementById: () => note, createElement: () => new Element(), createTextNode: value => value};
let saves = 0;
const SearchConfig = {save: () => { saves++; }};
''' + 'const configLimits = ' + js + '''
renderSources();
assert.equal(container.querySelectorAll().length, 10);
for (const [i, name] of Object.keys(sourcePresets.indeed).entries()) {
  const preset = presets.children[i];
  assert.equal(preset.type, 'button'); preset.events.click();
  const selected = container.querySelectorAll().filter(e => e.checked).map(e => e.value).sort();
  assert.deepEqual(selected, [...sourcePresets.indeed[name]].sort());
}
assert.equal(saves, 3);
form.elements.platform.value = 'type'; renderSources();
assert.equal(presets.children.length, 0);
assert.equal(container.querySelectorAll().length, 2);
console.log('three source presets select exact sources without starting searches; platform switch isolated');
'''
    result = subprocess.run([require_node(), '-e', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    runner = Mock(return_value=0)
    manager = SearchRunManager(runner=runner)
    config = validate_config({'platform': 'indeed', 'sources': list(SOURCE_PRESETS['development'])})
    manager._run(config)
    argv = runner.call_args.args[0]
    selected = [argv[i + 1] for i, value in enumerate(argv) if value == '--keyword']
    assert selected == list(SOURCE_PRESETS['development'])
    assert not set(selected) & set(INFRA_SOURCES)


@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('budget,model_budget,early_stop,expected', [
    (3, 3, 15, 3), (6, 6, 15, 6), (3, 1, 15, 3), (6, 6, 1, 1),
])
def test_indeed_round_robin_detail_budget(tmp_path, incremental, mixed, budget, model_budget, early_stop, expected):
    a = IndeedSearchAdapter()
    development = SOURCE_PRESETS['development']
    selected = SOURCES if mixed else development  # mixed deliberately Infra first
    owners = {}
    def cards(source, page):
        assert page == 1
        jobs = [job_from_url(f'https://jp.indeed.com/viewjob?jk=fictional_{SOURCES.index(source)}_{i}', {}) for i in range(8)]
        owners.update({j.job_id: source for j in jobs})
        return jobs
    a.search_cards = Mock(side_effect=cards)
    a.job_detail = Mock(return_value={'title': '架空開発', 'responsibilities': '自社 AWS Lambda API の設計と開発。', 'salary': '600万円'})
    model = Mock(model_name='fictional')
    model.classify_jobs.side_effect = lambda jobs: {j.job_id: SearchEvaluation(verdict='POSSIBLE', summary='虚构') for j in jobs}
    with Database(tmp_path / 'fictional.db') as db:
        _, stats = run_search(a, SearchStore(db), model, keywords=selected,
            max_jobs=budget, max_model_jobs=model_budget, early_stop=early_stop,
            batch_size=1, pages_per_keyword=None if incremental else 2)
    # Incremental mode intentionally does not use fixed-page early_stop.
    count = budget if incremental else expected
    assert [owners[c.args[0].job_id] for c in a.job_detail.call_args_list] == list(development[:count])
    assert stats['details'] == count
    assert stats['model_jobs'] == min(count, model_budget)
    assert all(c.args[1] == 1 for c in a.search_cards.call_args_list)
    assert all(pages == [1] for pages in stats['source_pages'].values())


@pytest.mark.parametrize('safety_stop', [False, True])
def test_indeed_round_robin_cached_duplicate_empty_and_safety(tmp_path, safety_stop):
    from scout_agent.green_discovery import GreenSearchDOMPending, Stage
    a = IndeedSearchAdapter()
    sources = SOURCE_PRESETS['development']
    cached_url = 'https://jp.indeed.com/viewjob?jk=fictional_cached'
    def cards(source, page):
        index = sources.index(source)
        if index == 2 and safety_stop:
            raise GreenSearchDOMPending(Stage.SOURCE_URL, 'CHALLENGE')
        if index == 3:
            return []
        return [job_from_url(cached_url, {}), *[
            job_from_url(f'https://jp.indeed.com/viewjob?jk=fictional_{index}_{i}', {}) for i in range(5)]]
    a.search_cards = Mock(side_effect=cards)
    fields = {'title': '架空開発', 'responsibilities': '自社 AWS Lambda API を開発。', 'salary': '600万円'}
    a.job_detail = Mock(return_value=fields)
    model = Mock(model_name='fictional')
    model.classify_jobs.side_effect = lambda jobs: {j.job_id: SearchEvaluation(verdict='POSSIBLE', summary='虚构') for j in jobs}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        cached = job_from_url(cached_url, fields)
        store.save_job(cached)
        store.save_result(cached, POLICY_VERSION, SearchEvaluation(verdict='TARGET', summary='保留缓存'), 'codex', 'fictional')
        store.set_user_state(cached.job_id, 'EXCLUDED')
        kwargs = dict(keywords=sources, max_jobs=6, max_model_jobs=3, batch_size=1, pages_per_keyword=None)
        if safety_stop:
            with pytest.raises(GreenSearchDOMPending) as exc:
                run_search(a, store, model, **kwargs)
            assert exc.value.reason == 'CHALLENGE'
            assert [c.args[0] for c in a.search_cards.call_args_list] == list(sources[:3])
            assert a.job_detail.call_count == 2
        else:
            _, stats = run_search(a, store, model, **kwargs)
            ids = [c.args[0].external_job_id for c in a.job_detail.call_args_list]
            assert ids[:5] == [f'fictional_{i}_0' for i in (0, 1, 2, 4, 5)]
            assert len(set(ids)) == stats['details'] == 6
            assert stats['cache_hits'] == 1 and stats['model_jobs'] == 3
        assert store.user_states()[cached.job_id] == 'EXCLUDED'
        assert store.cached(cached, POLICY_VERSION).summary == '保留缓存'


def test_documented_indeed_live_discovery_command_and_checks(capsys):
    from scout_agent.platform_discovery import main, evidence
    from test_indeed_discovery import snapshot
    readme = Path('README.md').read_text()
    command = 'python -m scout_agent.platform_discovery --cdp-endpoint http://127.0.0.1:9222 --platform indeed'
    assert command in readme
    assert '`search indeed --probe` **不支持**' in readme
    # argparse validates platform/endpoint and exits before importing Playwright.
    with pytest.raises(SystemExit) as exc:
        main(command.split()[3:] + ['--help'])
    assert exc.value.code == 0
    assert '--cdp-endpoint' in capsys.readouterr().out
    a = IndeedSearchAdapter()
    for source in GENERAL_DEV_SOURCES:
        result = evidence('indeed', a.source_url(source), snapshot(links=[JD]))
        assert result['source_route'] == 'jobs-query'
        assert result['query_keys_present']['q'] and result['jk_link_count'] == 1
        assert source in readme
    good = evidence('indeed', JD, snapshot(canonical=JD))
    assert good['stable_identity_candidate'] == 'jk'
    assert good['canonical_pattern'] == '/viewjob?jk=:id'
    bad = evidence('indeed', JD, snapshot(canonical='https://jp.indeed.com/viewjob?jk=fictional_wrong'))
    assert bad['stable_identity_candidate'] is None and bad['canonical_pattern'] is None
    for key in ('source_route', 'jk_link_count', 'stable_identity_candidate', 'canonical_pattern'):
        assert key in readme
