"""All companies, jobs, DOM and model outputs here are fictional."""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from scout_agent.cli import main
from scout_agent.search import (
    Job, SearchStore, SearchEvaluation, SearchCodex, GreenSearchAdapter,
    GreenSearchDOMPending, compact_jd, content_hash, list_drop, detail_drop,
    run_search, render_search, generate_search_report, parse_job_sections,
    read_green_detail, parse_search_cards,
)
from scout_agent.storage.db import Database
from scout_agent.web.app import create_app


def job(n=1, title='SRE', **fields):
    return Job.from_url(f'/company/900001/job/{n}', {
        'company': 'Fictional Cloud Lab', 'title': title,
        'salary': '500〜800万円', 'responsibilities': 'AWS 基盤の設計運用', **fields})


class FakeGreen:
    def __init__(self, dataset):
        self.dataset = dataset
        self.reads = []
        self.pages = []

    def search_cards(self, keyword, page):
        self.pages.append((keyword, page))
        return [Job(j.job_id, j.url, dict(j.fields)) for j in self.dataset.get((keyword, page), [])]

    def job_detail(self, card):
        self.reads.append(card.job_id)
        return card.fields


class FakeCodex:
    def __init__(self):
        self.batches = []

    def classify_jobs(self, jobs):
        self.batches.append([j.job_id for j in jobs])
        return {j.job_id: SearchEvaluation(verdict='POSSIBLE', summary='基础设施职责值得人工判断。',
                                          concerns=['Remote 信息未知。']) for j in jobs}


@pytest.fixture
def store(tmp_path):
    with Database(tmp_path / 'fictional.db') as db:
        yield SearchStore(db)


def run(store, jobs, **kwargs):
    adapter, model = FakeGreen({('AWS', 1): jobs}), FakeCodex()
    results, stats = run_search(adapter, store, model, keywords=['AWS'], **kwargs)
    return results, stats, adapter, model


def test_multiple_keywords_deduplicate(store):
    adapter = FakeGreen({('AWS', 1): [job()], ('SRE', 1): [job()]})
    model = FakeCodex()
    results, stats = run_search(adapter, store, model, keywords=['AWS', 'SRE'])
    assert stats['raw_cards'] == 2 and stats['unique_jobs'] == 1
    assert len(adapter.reads) == 1 and stats['model_jobs'] == 1
    assert results[0][0].matched_keywords == ['AWS', 'SRE']
    assert store.current_results()[0][0].matched_keywords == ['AWS', 'SRE']


@pytest.mark.parametrize('title', ['Frontend', 'iOS', 'Android', 'Designer', 'Sales', 'HR', 'Marketing', 'QA-only', 'Helpdesk-only', 'Backend-only'])
def test_list_zero_token_drop(store, title):
    _, stats, adapter, model = run(store, [job(title=title)])
    assert stats['list_drops'] == 1 and stats['DROP'] == 1
    assert adapter.reads == model.batches == []


@pytest.mark.parametrize('title', ['インフラ', 'ITエンジニア', '社内SE', 'サーバー', 'Network', 'Backend + Cloud', 'AWS Frontend', 'Platform Engineer'])
def test_ambiguous_titles_survive(title):
    assert not list_drop(title)


@pytest.mark.parametrize('fields,expected', [
    ({'salary': '300〜449万円'}, True), ({'salary': '最大 400万円'}, True),
    ({'salary': '450〜600万円'}, False), ({'salary': '400万円以上'}, False),
    ({'responsibilities': '客先常駐が中心'}, True),
    ({'responsibilities': 'SES案件に配属'}, True),
    ({'responsibilities': '客先常駐なし。自社 AWS 基盤'}, False),
    ({'responsibilities': 'ヘルプデスクのみ'}, True),
    ({'responsibilities': '監視業務のみ'}, True),
    ({'responsibilities': 'バックエンド開発のみ。AWS 環境'}, True),
    ({'responsibilities': 'AWS 基盤設計', 'required': 'Kubernetes/EKS 未経験可'}, False),
    ({'responsibilities': 'AWS 基盤設計', 'required': 'ArgoCD/Istio/observability 経験歓迎'}, False),
])
def test_detail_rules(fields, expected):
    assert bool(detail_drop(fields)) is expected


def test_detail_drop_zero_model_calls(store):
    _, stats, _, model = run(store, [job(salary='300〜400万円'), job(2, responsibilities='客先常駐が中心')])
    assert stats['detail_drops'] == 2 and stats['model_jobs'] == 0 and model.batches == []


def test_compact_noise_and_duplicates():
    fields = parse_job_sections('ナビゲーション\nHOME\n仕事内容\nAWS 基盤設計\nAWS 基盤設計\n必須要件\nLinux\nおすすめの求人\nNOISY AD\n会社概要\nFOOTER', title='SRE')
    fields.update({'html': '<nav>HOME</nav>', 'navigation': 'NOISY AD', 'footer': 'FOOTER'})
    compact = compact_jd(fields)
    payload = json.dumps(compact)
    assert 'HOME' not in payload and 'NOISY' not in payload and 'FOOTER' not in payload
    assert compact['responsibilities'] == 'AWS 基盤設計'
    assert len(payload) < len(json.dumps(fields))
    assert content_hash(fields) == content_hash(compact)


def test_cache_repeat_zero_calls_and_jd_policy_changes(store):
    _, first, _, _ = run(store, [job()])
    _, repeat, _, model = run(store, [job()])
    assert first['model_jobs'] == 1 and repeat['cache_hits'] == 1
    assert repeat['model_jobs'] == repeat['batches'] == 0 and model.batches == []
    _, changed, _, _ = run(store, [job(responsibilities='AWS 基盤設計と Terraform 構築')])
    assert changed['cache_hits'] == 0 and changed['model_jobs'] == 1
    _, policy, _, _ = run(store, [job()], policy_version='fictional-policy-v2')
    assert policy['cache_hits'] == 0 and policy['model_jobs'] == 1
    assert store.conn.execute('SELECT COUNT(*) FROM search_evaluations').fetchone()[0] == 3


def test_batch_budget_and_max_details(store):
    _, stats, adapter, model = run(store, [job(i) for i in range(1, 31)], max_jobs=12, max_model_jobs=10, batch_size=8)
    assert stats['details'] == len(adapter.reads) == 12
    assert stats['model_jobs'] == 10 and stats['batches'] == 2
    assert list(map(len, model.batches)) == [8, 2]
    assert stats['deferred'] == 3


def test_early_stop(store):
    _, stats, adapter, _ = run(store, [job(i) for i in range(1, 51)], batch_size=5)
    assert stats['POSSIBLE'] == stats['details'] == 15
    assert len(adapter.reads) == 15 and adapter.pages == [('AWS', 1)]


def test_pagination_bounded(store):
    adapter = FakeGreen({('AWS', p): [job(p, title='Frontend')] for p in range(1, 10)})
    _, stats = run_search(adapter, store, FakeCodex(), keywords=['AWS'], pages_per_keyword=2)
    assert adapter.pages == [('AWS', 1), ('AWS', 2)] and stats['raw_cards'] == 2


def test_report_and_web_drop_count_only(store, tmp_path):
    results, stats, _, _ = run(store, [job(), job(2, title='Frontend')])
    html = render_search(results, stats)
    assert 'POSSIBLE' in html and 'DROP：1' in html and 'Frontend' not in html
    assert 'matched keyword：AWS' in html and '来源：search' in html
    assert 'Remote：UNKNOWN' in html and '/company/900001/job/1' in html
    paths = generate_search_report(tmp_path / 'fictional-output', results, stats)
    assert json.loads(paths[1].read_text())['source_kind'] == 'search'
    before = store.conn.total_changes
    with TestClient(create_app(Path_from_store(store))) as client:
        response = client.get('/search')
    assert response.status_code == 200 and 'POSSIBLE' in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert store.conn.total_changes == before


def Path_from_store(store):
    from pathlib import Path
    return Path(store.conn.execute('PRAGMA database_list').fetchone()[2])


def test_old_database_and_scout_preservation(tmp_path):
    from scout_agent.models.scout import Scout
    from scout_agent.models.evaluation import Evaluation
    path = tmp_path / 'fictional-old.db'
    conn = sqlite3.connect(path)
    conn.executescript('CREATE TABLE scouts(id INTEGER PRIMARY KEY,platform TEXT NOT NULL,dedupe_key TEXT NOT NULL,external_id TEXT,url TEXT,payload TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(platform,dedupe_key)); CREATE TABLE evaluations(id INTEGER PRIMARY KEY,scout_id INTEGER NOT NULL UNIQUE,run_id INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);')
    conn.close()
    with Database(path) as db:
        sid = db.save_scout(Scout(id='existing-fictional', platform='green', job_title='SRE'))
        rid = db.start_run('green')
        db.save_evaluation(sid, rid, Evaluation(verdict='KEEP', confidence=0.8, summary='虚构已有评价。'), provider='codex', model_name='fictional')
        before = [tuple(r) for r in db.conn.execute('SELECT * FROM evaluations')]
        scouts = [tuple(r) for r in db.conn.execute('SELECT * FROM scouts')]
        run(SearchStore(db), [job()])
        assert [tuple(r) for r in db.conn.execute('SELECT * FROM evaluations')] == before
        assert [tuple(r) for r in db.conn.execute('SELECT * FROM scouts')] == scouts
    with TestClient(create_app(tmp_path / 'missing.db')) as client:
        assert client.get('/search').status_code == 200
    assert not (tmp_path / 'missing.db').exists()


def test_old_web_database_no_search_migration(tmp_path):
    path = tmp_path / 'fictional.db'
    with Database(path):
        pass
    with TestClient(create_app(path)) as client:
        assert client.get('/search').status_code == 200
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='search_jobs'").fetchone()


def test_safe_failure_no_browser_db_env_model(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError('forbidden external action')
    monkeypatch.setattr('scout_agent.cli.load_settings', forbidden)
    monkeypatch.setattr('scout_agent.browser.manager.BrowserManager.open', forbidden)
    monkeypatch.setattr('scout_agent.search.SearchCodex._run_codex', forbidden)
    assert main(['search', 'green', '--max-jobs', '30']) == 2
    assert 'DOM 尚未验证' in capsys.readouterr().err
    with pytest.raises(GreenSearchDOMPending):
        GreenSearchAdapter().ensure_verified()


def test_details_navigation_read_only():
    events = []
    class Node:
        @property
        def first(self):
            return self
        def wait_for(self, **kwargs):
            events.append('read')
        def locator(self, selector):
            events.append('read')
            return self
        def inner_text(self):
            events.append('read')
            return '仕事内容\nAWS 基盤設計\n必須要件\nLinux'
    class Page:
        url = job().url
        def goto(self, url, **kwargs):
            events.append('navigate')
            self.url = url
        def get_by_role(self, *args, **kwargs):
            return Node()
        def locator(self, selector):
            return Node()
    assert read_green_detail(Page(), job())['responsibilities'] == 'AWS 基盤設計'
    assert set(events) == {'read', 'navigate'}


@pytest.mark.parametrize('url', ['https://evil.test/company/1/job/2', 'http://www.green-japan.com/company/1/job/2', '/messages/v2/1', '/company/1/job/2/apply'])
def test_url_boundary(url):
    with pytest.raises(ValueError):
        Job.from_url(url, {})


def test_codex_batch_compact_mocked_executor(monkeypatch):
    calls = []
    def fake(self, prompt, schema):
        calls.append(prompt)
        return json.dumps([{'job_id': job(n).job_id, 'evaluation': {'verdict': 'POSSIBLE', 'summary': '主要职责值得人工判断。', 'reasons': [], 'concerns': ['经验要求需确认。']}} for n in [1, 2]])
    monkeypatch.setattr(SearchCodex, '_run_codex', fake)
    model = SearchCodex(None, '', 'low')
    assert len(model.classify_jobs([job(1, navigation='NOISY'), job(2)])) == 2
    assert len(calls) == 1 and 'NOISY' not in calls[0] and 'Kubernetes/EKS' in calls[0]
    assert model.reasoning_effort == 'low'


def test_incomplete_batch_not_saved(store):
    model = Mock()
    model.classify_jobs.return_value = {}
    with pytest.raises(ValueError):
        run_search(FakeGreen({('AWS', 1): [job()]}), store, model, keywords=['AWS'])
    assert store.current_results() == []


def test_fifty_cards_low_token_funnel_and_repeat(store):
    jobs = [job(i, title='Frontend') for i in range(1, 26)]
    jobs += [job(i, salary='300〜400万円') for i in range(26, 36)]
    jobs += [job(i) for i in range(36, 51)]
    _, first, _, _ = run(store, jobs)
    _, second, _, _ = run(store, jobs)
    assert first['raw_cards'] == first['unique_jobs'] == 50
    assert first['list_drops'] == 25 and first['detail_drops'] == 10
    assert first['model_jobs'] == 15 and first['batches'] == 2
    assert second['cache_hits'] == 25 and second['model_jobs'] == second['batches'] == 0
    print('fictional 50 cards: local=35, model=15, batches=2; repeat: cache=25, model=0, batches=0')


def test_structured_card_parser_fictional():
    cards = parse_search_cards([{'url': '/company/900001/job/42', 'title': '社内SE',
                                'company': 'Fictional Team', 'noise': 'NAVIGATION'}])
    assert cards[0].job_id == '900001:42'
    assert cards[0].fields['title'] == '社内SE'
    assert 'noise' not in cards[0].fields


def test_fixed_annual_salary():
    assert detail_drop({'salary': '年収 400万円'})
    assert detail_drop({'salary': '年収 400万円以上'}) is None


def test_sparse_keywords_share_one_batch(store):
    adapter = FakeGreen({('AWS', 1): [job(1)], ('SRE', 1): [job(2)]})
    model = FakeCodex()
    _, stats = run_search(adapter, store, model, keywords=['AWS', 'SRE'])
    assert stats['model_jobs'] == 2 and stats['batches'] == 1
    assert list(map(len, model.batches)) == [2]


def test_monthly_salary_not_annual_ceiling():
    assert detail_drop({'salary': '月給 30〜40万円'}) is None
