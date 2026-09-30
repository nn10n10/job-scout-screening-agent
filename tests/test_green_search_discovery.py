"""Fictional DOM only: never access browsers, recruitment sites or models."""
from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import sys

import pytest

from scout_agent.green_discovery import (
    SOURCES, ORIGIN, Job, GreenSearchAdapter, GreenSearchDOMPending,
    pagination_url, source_url, probe_command,
)


class Node:
    def __init__(self, texts=(), href=None):
        self.texts, self.href = list(texts), href
    @property
    def first(self):
        return self
    def get_attribute(self, key):
        assert key == 'href'
        return self.href
    def locator(self, selector):
        assert selector in ('p:visible', 'xpath=../..')
        return self
    def all_inner_texts(self):
        return self.texts
    def inner_text(self):
        return '\n'.join(self.texts)
    def wait_for(self, **kwargs):
        pass


class Links:
    def __init__(self, nodes):
        self.nodes = nodes
    @property
    def first(self):
        return Node()
    def all(self):
        return self.nodes


class Page:
    def __init__(self, nodes=(), counts=('求人 2件',), detail_title='Fictional SRE', duties='仕事内容\nAWS 基盤設計'):
        self.nodes, self.counts = nodes, counts
        self.detail_title, self.duties = detail_title, duties
        self.events = []
        self.url = ''
    def goto(self, url, **kwargs):
        self.url = url
        self.events.append(('goto', url))
    def locator(self, selector):
        self.events.append(('read', selector))
        if selector == 'h1':
            return Node([self.detail_title])
        assert selector == 'a[href*="/company/"][href*="/job/"]:visible'
        return Links(self.nodes)
    def get_by_text(self, pattern):
        self.events.append(('read', 'counts'))
        return Node(self.counts)
    def get_by_role(self, role, **kwargs):
        assert role == 'heading' and kwargs == {'name': '仕事内容', 'exact': True}
        self.events.append(('read', 'heading'))
        return Node([self.duties])
    def close(self):
        self.closed = True


def adapter(page):
    result = GreenSearchAdapter()
    result.page = page
    return result


@pytest.mark.parametrize('label,path', list(SOURCES.items()))
def test_source_mapping(label, path):
    assert source_url(label) == ORIGIN + path
    assert source_url(label, 2) == ORIGIN + path + '?page=2'


def test_pagination_preserves_other_parameters():
    base = ORIGIN + '/search/skill/AWS?sort=test&empty=&page=9'
    assert pagination_url(base, 1) == ORIGIN + '/search/skill/AWS?sort=test&empty='
    assert pagination_url(base, 2) == ORIGIN + '/search/skill/AWS?sort=test&empty=&page=2'


@pytest.mark.parametrize('url', ['http://www.green-japan.com/company/1/job/2',
    'https://www.green-japan.com.evil.test/company/1/job/2',
    'https://user@www.green-japan.com/company/1/job/2',
    '//evil.test/company/1/job/2', '/company/1/job/2/apply',
    '\n/company/1/job/2', '/company/1/job/2\t', '/company/１/job/2', '/company/1/job/2/', '/company/1/job/2%2fapply'])
def test_job_boundary(url):
    with pytest.raises(ValueError):
        Job.from_url(url, {})


@pytest.mark.parametrize('label', ['Platform Engineer', 'クラウドエンジニア', 'unknown?keyword=AWS'])
def test_unknown_source_cannot_navigate(label):
    page = Page()
    with pytest.raises(ValueError):
        adapter(page).search_cards(label, 1)
    assert page.events == []


def test_cards_deduplicate_and_unknown():
    page = Page([
        Node(['Fictional SRE', '500〜800万円', '東京都'], '/company/900001/job/1'),
        Node([], ORIGIN + '/company/900001/job/1?tracking=fictional'),
        Node([], '/company/900001/job/2'),
        Node(['bad'], 'https://evil.test/company/900001/job/3'),
    ])
    a = adapter(page)
    jobs = a.search_cards('AWS', 1)
    assert len(jobs) == 2
    assert jobs[0].fields == {'title': 'Fictional SRE', 'salary': '500〜800万円', 'location': '東京都'}
    assert jobs[1].fields == {'title': '', 'salary': '', 'location': ''}
    assert a.last_stats == dict(cards=4, valid_job_links=3, title=1, salary=1, location=1)
    assert '?' not in jobs[0].url


@pytest.mark.parametrize('counts', [('求人 12件',), (), ('求人 0件', '求人 2件'), ('応募 0件',), ('noise\n0件',)])
def test_positive_or_unknown_layout_fails_closed(counts):
    with pytest.raises(GreenSearchDOMPending):
        adapter(Page(counts=counts)).search_cards('SRE', 1)


def test_zero_results():
    assert adapter(Page(counts=['検索結果 0件'])).search_cards('SRE', 1) == []


def test_locator_failure_never_full_page_fallback():
    page = Page()
    def broken(selector):
        raise ValueError('fictional locator failure')
    page.locator = broken
    with pytest.raises(ValueError):
        adapter(page).search_cards('AWS', 1)
    assert all(action == 'goto' for action, _ in page.events)


def settings():
    # No DB, report or model configuration exists; any access would fail.
    return SimpleNamespace(browser_mode='cdp', profile_path='unused', cdp_endpoint='http://fictional.invalid')


def attach(monkeypatch, page):
    @contextmanager
    def fake_open(self):
        yield SimpleNamespace(contexts=[SimpleNamespace(new_page=lambda: page)])
    monkeypatch.setattr('scout_agent.browser.manager.BrowserManager.open', fake_open)


def test_probe_bounded_read_only_private_output(monkeypatch, capsys):
    page = Page([Node(['PRIVATE TITLE', '500〜800万円', '東京都'], f'/company/900001/job/{n}') for n in range(1, 12)])
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=None, max_jobs=None), settings()) == 0
    output = capsys.readouterr().out
    assert 'source=AWS' in output and 'detail_responsibilities=5' in output
    assert 'PRIVATE' not in output and '/company/' not in output and '東京都' not in output
    assert len([event for event in page.events if event[0] == 'goto']) == 6
    assert {event[0] for event in page.events} == {'goto', 'read'}
    assert page.closed


@pytest.mark.parametrize('title,duties', [('', '仕事内容\nAWS'), ('SRE', '仕事内容'), ('SRE', 'noise')])
def test_probe_missing_detail_fails(monkeypatch, capsys, title, duties):
    page = Page([Node([], '/company/900001/job/1')], detail_title=title, duties=duties)
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=5), settings()) == 1
    assert 'probe 失败' in capsys.readouterr().err
    assert page.closed


def test_probe_zero_results_fails(monkeypatch, capsys):
    attach(monkeypatch, Page(counts=['求人 0件']))
    assert probe_command(SimpleNamespace(keyword=None, max_jobs=5), settings()) == 1
    assert 'probe 失败' in capsys.readouterr().err


def test_probe_fresh_process_does_not_import_providers_db_report(tmp_path):
    script = '''
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from scout_agent.cli import main
import scout_agent.cli as cli
from scout_agent.browser.manager import BrowserManager
class Node:
    @property
    def first(self): return self
    def wait_for(self, **kwargs): pass
    def all(self): return []
    def all_inner_texts(self): return ['求人 0件']
class Page:
    def goto(self, url, **kwargs): self.url = url
    def locator(self, selector): return Node()
    def get_by_text(self, pattern): return Node()
    def close(self): pass
@contextmanager
def fake_open(self):
    yield SimpleNamespace(contexts=[SimpleNamespace(new_page=lambda: Page())])
BrowserManager.open = fake_open
cli.load_settings = lambda: SimpleNamespace(browser_mode='cdp', profile_path='unused', cdp_endpoint='http://fictional.invalid')
assert main(['search', 'green', '--probe']) == 1
for name in sys.modules:
    assert not name.startswith(('scout_agent.llm.codex', 'scout_agent.llm.gemini', 'scout_agent.llm.mock', 'scout_agent.storage', 'scout_agent.report')), name
print('probe isolation: no provider/DB/report import')
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'probe isolation:' in result.stdout


def test_probe_cdp_only(monkeypatch):
    s = settings()
    s.browser_mode = 'persistent'
    def forbidden(*args):
        raise AssertionError('must not open browser')
    monkeypatch.setattr('scout_agent.browser.manager.BrowserManager.open', forbidden)
    assert probe_command(SimpleNamespace(keyword=None, max_jobs=5), s) == 1


def test_search_redirect_fails_before_dom_reads():
    page = Page()
    def redirected(url, **kwargs):
        page.url = 'https://fictional.invalid/search'
    page.goto = redirected
    with pytest.raises(GreenSearchDOMPending):
        adapter(page).search_cards('AWS', 1)
    assert page.events == []


def test_zero_result_after_link_wait_timeout():
    from playwright.sync_api import TimeoutError
    page = Page(counts=['検索結果 0件'])
    original_locator = page.locator
    class TimedLinks(Links):
        @property
        def first(self):
            node = Node()
            def timeout(**kwargs):
                raise TimeoutError('fictional timeout')
            node.wait_for = timeout
            return node
    def locator(selector):
        original_locator(selector)
        return TimedLinks([])
    page.locator = locator
    assert adapter(page).search_cards('AWS', 1) == []


def test_probe_budget_rejected_before_browser(monkeypatch):
    def forbidden(*args):
        raise AssertionError('must not open browser')
    monkeypatch.setattr('scout_agent.browser.manager.BrowserManager.open', forbidden)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=6), settings()) == 1


def test_keyword_cli_rejects_unverified_label(capsys):
    from scout_agent.cli import main
    with pytest.raises(SystemExit) as exc:
        main(['search', 'green', '--probe', '--keyword', 'Platform Engineer'])
    assert exc.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err


def test_enabled_adapter_keeps_cache_batch_and_budgets(tmp_path):
    from scout_agent.search import run_search, SearchStore, SearchEvaluation
    from scout_agent.storage.db import Database
    page = Page([Node(['Fictional SRE', '500〜800万円', '東京都'],
                      f'/company/900001/job/{n}') for n in range(1, 6)])
    class Model:
        calls = []
        def classify_jobs(self, jobs):
            self.calls.append(len(jobs))
            return {job.job_id: SearchEvaluation(verdict='POSSIBLE', summary='基础设施职责值得判断。') for job in jobs}
    model = Model()
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        _, stats = run_search(adapter(page), store, model, keywords=['AWS'], max_jobs=3,
                              max_model_jobs=2, batch_size=2)
        assert stats['details'] == 3 and stats['model_jobs'] == 2 and stats['batches'] == 1
        assert model.calls == [2]
        _, repeat = run_search(adapter(page), store, model, keywords=['AWS'], max_jobs=2,
                              max_model_jobs=2, batch_size=2)
        assert repeat['cache_hits'] == 2 and repeat['model_jobs'] == repeat['batches'] == 0
        assert model.calls == [2]
