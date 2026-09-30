"""Fictional DOM only: never access browsers, recruitment sites or models."""
from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import sys

import pytest

from scout_agent.green_discovery import (
    SOURCES, ORIGIN, Job, GreenSearchAdapter, GreenSearchDOMPending,
    pagination_url, source_url, probe_command, valid_source_redirect,
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
    with pytest.raises(GreenSearchDOMPending):
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


@pytest.mark.parametrize('suffix', ['?canonical=fictional', '/?canonical=fictional', '?extra=1&page=2'])
@pytest.mark.parametrize('number', [1, 2])
def test_source_canonical_redirect(suffix, number):
    page = Page(counts=['0求人'])
    if number >= 2 and 'page=' not in suffix:
        suffix += '&page=2'
    page.goto = lambda url, **kwargs: setattr(page, 'url', source_url('AWS') + suffix)
    assert adapter(page).search_cards('AWS', number) == []


@pytest.mark.parametrize('path,number,accepted', [
    ('/search?opaque=fictional', 1, True),
    ('/search?opaque=fictional&page=2', 2, True),
    ('/search?opaque=fictional', 2, False),
    ('/search?opaque=fictional&page=1', 2, False),
    ('/search?opaque=fictional&page=2&page=3', 2, False),
    ('/search?opaque=fictional&page=', 2, False),
    ('/search/skill/AWS?opaque=fictional', 2, False),
    ('/search/skill/AWS?page=1', 2, False),
    ('/search/skill/AWS?page=2&page=1', 2, False),
    ('/search', 1, False), ('/search?', 1, False),
    ('/search/?opaque=fictional', 1, False),
    ('/login?opaque=fictional', 1, False),
    ('/messages?opaque=fictional', 1, False),
    ('/search/skill/SRE?opaque=fictional', 1, False),
])
def test_canonical_search_pagination_boundary(path, number, accepted):
    assert valid_source_redirect(ORIGIN + path, 'AWS', number) is accepted
    page = Page(counts=['0求人'])
    def goto(target, **kwargs):
        assert target == source_url('AWS', number)
        page.url = ORIGIN + path
    page.goto = goto
    if accepted:
        assert adapter(page).search_cards('AWS', number) == []
    else:
        with pytest.raises(GreenSearchDOMPending) as exc:
            adapter(page).search_cards('AWS', number)
        assert exc.value.stage.value == 'SOURCE_URL'
        assert not page.events


def test_probe_opaque_canonical_search_passes(monkeypatch, capsys):
    page = Page([Node(['Fictional SRE'], '/company/900001/job/1')])
    original = page.goto
    def goto(target, **kwargs):
        original(target, **kwargs)
        if target == source_url('AWS'):
            page.url = ORIGIN + '/search?opaque=PRIVATE_QUERY'
    page.goto = goto
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=2), settings()) == 0
    output = capsys.readouterr()
    assert 'valid_job_links=1' in output.out
    assert 'SOURCE_URL' not in output.err
    assert 'PRIVATE_QUERY' not in output.out + output.err
    assert all(event[0] in ('goto', 'read') for event in page.events)


@pytest.mark.parametrize('url', [
    'http://www.green-japan.com/search/skill/AWS',
    'https://www.green-japan.com.evil.test/search/skill/AWS',
    'https://user@www.green-japan.com/search/skill/AWS',
    ORIGIN + '/login', ORIGIN + '/search/skill/SRE',
    ORIGIN + '/search/skill/AWS//', ORIGIN + '/search/skill/AWS%2f',
    'http://www.green-japan.com/search?opaque=fictional',
    'https://evil.test/search?opaque=fictional',
    'https://user@www.green-japan.com/search?opaque=fictional',
    ORIGIN + '/search', ORIGIN + '/search?', ORIGIN + '/messages?opaque=fictional',
])
def test_probe_source_url_diagnostic(monkeypatch, capsys, url):
    page = Page()
    page.goto = lambda target, **kwargs: setattr(page, 'url', url)
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=5), settings()) == 1
    output = capsys.readouterr().err
    assert 'source=AWS stage=SOURCE_URL reason=SOURCE_URL_MISMATCH' in output
    assert url not in output
    assert not page.events


@pytest.mark.parametrize('count', ['8715件ヒットしました。', '８，７１５件ヒットしました。',
                                     '検索結果 2015企業 8715求人', '検索結果 ２，０１５企業 ８，７１５求人'])
def test_observed_count_positive_fails_closed(count):
    with pytest.raises(GreenSearchDOMPending) as exc:
        adapter(Page(counts=[count])).search_cards('AWS', 1)
    assert exc.value.reason == 'NO_VALID_JOB_LINKS'


@pytest.mark.parametrize('count', ['0件ヒットしました。', '検索結果 12企業 0求人', '０求人'])
def test_observed_count_zero(count):
    assert adapter(Page(counts=[count])).search_cards('AWS', 1) == []


@pytest.mark.parametrize('title,duties,stage,reason', [
    ('', '仕事内容\nfictional', 'DETAIL_TITLE', 'TITLE_MISSING'),
    ('PRIVATE TITLE', '仕事内容', 'DETAIL_RESPONSIBILITIES', 'RESPONSIBILITIES_MISSING'),
])
def test_probe_detail_diagnostics(monkeypatch, capsys, title, duties, stage, reason):
    page = Page([Node([], '/company/900001/job/1')], detail_title=title, duties=duties)
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=5), settings()) == 1
    output = capsys.readouterr().err
    assert f'source=AWS stage={stage} reason={reason}' in output
    assert 'cards=1 valid_job_links=1' in output
    assert 'PRIVATE' not in output and '/company/' not in output


def test_probe_unknown_count_diagnostic(monkeypatch, capsys):
    attach(monkeypatch, Page(counts=['PRIVATE noise']))
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=5), settings()) == 1
    output = capsys.readouterr().err
    assert 'stage=JOB_LINKS reason=UNKNOWN_RESULT_COUNT cards=0' in output
    assert 'PRIVATE' not in output


@pytest.mark.parametrize('stage', ['SOURCE_NAVIGATION', 'JOB_LINKS', 'RESULT_COUNT',
                                    'DETAIL_NAVIGATION', 'DETAIL_TITLE', 'DETAIL_RESPONSIBILITIES'])
@pytest.mark.parametrize('timeout', [False, True])
def test_probe_playwright_error_redaction(monkeypatch, capsys, stage, timeout):
    from playwright.sync_api import Error, TimeoutError
    page = Page([Node([], '/company/900001/job/1')])
    def fail(*args, **kwargs):
        raise (TimeoutError if timeout else Error)('PRIVATE JD https://private.invalid/?token=secret')
    if stage == 'SOURCE_NAVIGATION':
        page.goto = fail
    elif stage == 'DETAIL_NAVIGATION':
        original = page.goto
        def goto(url, **kwargs):
            if '/company/' in url:
                fail()
            original(url, **kwargs)
        page.goto = goto
    elif stage == 'RESULT_COUNT':
        page.get_by_text = fail
    elif stage == 'DETAIL_RESPONSIBILITIES':
        page.get_by_role = fail
    else:
        original = page.locator
        def locator(selector):
            if (stage == 'JOB_LINKS' and selector != 'h1') or (stage == 'DETAIL_TITLE' and selector == 'h1'):
                fail()
            return original(selector)
        page.locator = locator
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS'], max_jobs=5), settings()) == 1
    output = capsys.readouterr().err
    reason = 'PLAYWRIGHT_TIMEOUT' if timeout else 'PLAYWRIGHT_ERROR'
    assert f'stage={stage} reason={reason}' in output
    assert 'PRIVATE' not in output and 'secret' not in output


def test_probe_empty_source_continues_with_total_budget(monkeypatch, capsys):
    page = Page([Node([], f'/company/900001/job/{n}') for n in range(1, 9)])
    original = page.goto
    def goto(url, **kwargs):
        original(url, **kwargs)
        if '/search/' in url:
            page.counts = ['0求人'] if '/AWS' in url else ['8件ヒットしました。']
            page.nodes = [] if '/AWS' in url else [Node([], f'/company/900001/job/{n}') for n in range(1, 9)]
    page.goto = goto
    attach(monkeypatch, page)
    assert probe_command(SimpleNamespace(keyword=['AWS', 'SRE'], max_jobs=5), settings()) == 0
    output = capsys.readouterr().out
    assert 'source=AWS cards=0' in output and 'source=SRE' in output
    assert len([url for action, url in page.events if action == 'goto' and '/company/' in url]) == 5


@pytest.mark.parametrize('reason,expected', [('SOURCE_URL_MISMATCH', 'SOURCE_URL_MISMATCH'),
                                           ('PRIVATE JD token=secret', 'UNKNOWN')])
def test_search_safety_stop_last_progress_context(tmp_path, monkeypatch, capsys, reason, expected):
    from scout_agent.search import search_command
    page = Page(counts=['0求人'])
    attach(monkeypatch, page)
    a = adapter(page)
    def fail(source, number):
        if source == 'インフラエンジニア':
            raise GreenSearchDOMPending('SOURCE_URL', reason)
        return []
    a.search_cards = fail
    args = SimpleNamespace(keyword=['AWS', 'インフラエンジニア'], max_jobs=30,
                           max_model_jobs=20, pages_per_keyword=1)
    s = settings()
    s.db_path = tmp_path / 'fictional.db'
    s.codex_model, s.codex_batch_size = 'fictional', 2
    monkeypatch.setattr('scout_agent.search.SearchCodex', lambda *args: object())
    monkeypatch.setattr('scout_agent.search.generate_search_report',
                        lambda *args: pytest.fail('must not report a failed run'))
    assert search_command(args, s, adapter=a) == 1
    output = capsys.readouterr()
    assert output.out.splitlines()[-1] == 'Search progress: インフラエンジニア page 1'
    assert output.err.strip() == f'Green safety stop: source=インフラエンジニア page=1 category={expected}'
    assert 'PRIVATE' not in output.out + output.err and 'secret' not in output.err
    assert page.closed
