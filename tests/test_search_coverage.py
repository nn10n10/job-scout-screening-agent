"""Incremental coverage uses only fictional cards and mocked models."""
import pytest

from test_search import FakeGreen, FakeCodex, job, store
from scout_agent.search import run_search, SearchStore, render_search, POLICY_VERSION
from scout_agent.llm.codex import CodexClassifierError


def coverage(adapter, store, model=None, **kwargs):
    return run_search(adapter, store, model or FakeCodex(), keywords=['AWS'],
                      pages_per_keyword=None, **kwargs)


def dataset():
    return {('AWS', p): [job(p)] for p in range(1, 16)}


def test_rotation_and_known_zero_details(store):
    first = FakeGreen(dataset())
    _, stats = coverage(first, store)
    assert first.pages == [('AWS', 1), ('AWS', 2), ('AWS', 3)]
    assert stats['new_jobs'] == stats['model_jobs'] == 3
    assert stats['cursors']['AWS'] == {'before': 2, 'after': 4}
    assert stats['source_pages'] == {'AWS': [1, 2, 3]}
    second = FakeGreen(dataset())
    results, stats = coverage(second, store)
    assert second.pages == [('AWS', 1), ('AWS', 4), ('AWS', 5)]
    assert stats['source_pages'] == {'AWS': [1, 4, 5]}
    assert 'AWS pages: 1,4,5' in render_search(results, stats)
    assert second.reads == [job(4).job_id, job(5).job_id]
    assert stats['new_jobs'] == 2 and stats['known_jobs'] == stats['cache_hits'] == 1
    assert stats['model_jobs'] == 2 and stats['pages_scanned'] == 3
    assert 'NEW POSSIBLE' in render_search(results, stats)
    assert job(1).job_id not in [j.job_id for j, _ in results]


def test_source_independence_and_wrap(store):
    coverage(FakeGreen(dataset()), store, max_depth=3)
    assert store.source_cursor('AWS', 3) == 2
    assert store.conn.execute('SELECT cycle_count FROM search_source_state').fetchone()[0] == 1
    coverage(FakeGreen(dataset()), store)
    coverage(FakeGreen(dataset()), store)
    other = FakeGreen({('SRE', p): [job(100+p)] for p in range(1, 4)})
    run_search(other, store, FakeCodex(), keywords=['SRE'], pages_per_keyword=None)
    assert other.pages == [('SRE', 1), ('SRE', 2), ('SRE', 3)]
    assert store.source_cursor('AWS', 15) == 6
    assert store.source_cursor('SRE', 15) == 4


def test_failed_page_does_not_advance(store):
    coverage(FakeGreen(dataset()), store)
    class Broken(FakeGreen):
        def search_cards(self, keyword, page):
            if page == 4:
                raise RuntimeError('fictional failure')
            return super().search_cards(keyword, page)
    with pytest.raises(RuntimeError):
        coverage(Broken(dataset()), store)
    assert store.source_cursor('AWS', 15) == 4


def test_failed_batch_resumes_without_detail(store):
    class Broken(FakeCodex):
        def classify_jobs(self, jobs):
            raise CodexClassifierError('fictional', category='quota')
    with pytest.raises(CodexClassifierError):
        coverage(FakeGreen(dataset()), store, Broken())
    assert store.source_cursor('AWS', 15) == 2
    adapter = FakeGreen(dataset())
    _, stats = coverage(adapter, store)
    assert job(1).job_id not in adapter.reads
    assert stats['known_jobs'] == 1 and stats['model_jobs'] == 3


def test_empty_source_resets(store):
    store.advance_source('AWS', 8)
    adapter = FakeGreen({})
    _, stats = coverage(adapter, store)
    assert adapter.pages == [('AWS', 1)]
    assert stats['cursors']['AWS'] == {'before': 8, 'after': 2}


def test_budget_still_scans_all_sources_page_one(store):
    adapter = FakeGreen({('AWS', p): [job(p)] for p in range(1, 4)} |
                        {('SRE', p): [job(100+p)] for p in range(1, 4)})
    _, stats = run_search(adapter, store, FakeCodex(), keywords=['AWS', 'SRE'],
                         pages_per_keyword=None, max_jobs=1, max_model_jobs=1)
    assert adapter.pages == [('AWS', 1), ('AWS', 2), ('SRE', 1), ('SRE', 2)]
    assert stats['details'] == stats['model_jobs'] == 1 and stats['deferred'] == 3
    assert stats['cursors'] == {source: {'before': 2, 'after': 2} for source in ['AWS', 'SRE']}


def test_partial_deep_page_retries_before_later_pages(store):
    store.advance_source('AWS', 4)
    data = {('AWS', 1): [job(1, title='Frontend')],
            ('AWS', 4): [job(4), job(5), job(6)],
            ('AWS', 5): [job(7)], ('SRE', 1): [job(100)]}
    adapter, model = FakeGreen(data), FakeCodex()
    _, stats = run_search(adapter, store, model, keywords=['AWS', 'SRE'],
                         pages_per_keyword=None, max_jobs=1)
    assert adapter.pages == [('AWS', 1), ('AWS', 4), ('SRE', 1), ('SRE', 2)]
    assert stats['cursors']['AWS'] == {'before': 4, 'after': 4}
    assert adapter.reads == [job(4).job_id]
    assert model.batches == [[job(4).job_id]]
    for n in [5, 6, 7, 100]:
        assert store.existing_job(job(n).job_id) is None
    retry = FakeGreen(data)
    _, stats = coverage(retry, store)
    assert retry.pages == [('AWS', 1), ('AWS', 4), ('AWS', 5)]
    assert retry.reads == [job(n).job_id for n in [5, 6, 7]]
    assert stats['cursors']['AWS'] == {'before': 4, 'after': 6}


def test_history_unknown_and_last_seen_preserved(store):
    store.save_job(job())
    store.conn.execute('UPDATE search_jobs SET first_seen_at=NULL')
    store.conn.commit()
    SearchStore(type('DB', (), {'conn': store.conn})())
    assert store.conn.execute('SELECT first_seen_at FROM search_jobs').fetchone()[0] is None
    store.save_job(job())
    row = store.conn.execute('SELECT first_seen_at,last_seen_at FROM search_jobs').fetchone()
    assert row[0] is None and row[1]


@pytest.mark.parametrize('saved_details', [False, True])
def test_model_budget_holds_deep_cursor_and_resumes_saved_details(store, saved_details):
    store.advance_source('AWS', 4)
    candidates = [job(n) for n in [4, 5, 6]]
    if saved_details:
        for candidate in candidates:
            store.save_job(candidate)
    data = {('AWS', 1): [job(1, title='Frontend')],
            ('AWS', 4): candidates, ('AWS', 5): [job(7)],
            ('SRE', 1): [job(100)]}
    adapter, model = FakeGreen(data), FakeCodex()
    _, stats = run_search(adapter, store, model, keywords=['AWS', 'SRE'],
                         pages_per_keyword=None, max_model_jobs=1)
    assert adapter.pages == [('AWS', 1), ('AWS', 4), ('SRE', 1), ('SRE', 2)]
    assert stats['cursors']['AWS'] == {'before': 4, 'after': 4}
    assert stats['model_jobs'] == 1 and stats['deferred'] == 3
    assert adapter.reads == [job(n).job_id for n in ([100] if saved_details else [4, 5, 6, 100])]
    assert model.batches == [[job(4).job_id]]
    for candidate in candidates:
        existing = store.existing_job(candidate.job_id)
        assert existing.fields == candidate.fields
    assert store.cached(store.existing_job(job(4).job_id), POLICY_VERSION) is not None
    for n in [5, 6]:
        assert store.cached(store.existing_job(job(n).job_id), POLICY_VERSION) is None
    retry, resumed_model = FakeGreen(data), FakeCodex()
    _, stats = coverage(retry, store, resumed_model)
    assert retry.pages == [('AWS', 1), ('AWS', 4), ('AWS', 5)]
    assert retry.reads == [job(7).job_id]
    assert resumed_model.batches == [[job(5).job_id, job(6).job_id], [job(7).job_id]]
    assert stats['model_jobs'] == 3 and stats['cache_hits'] == 2
    assert stats['cursors']['AWS'] == {'before': 4, 'after': 6}


def test_model_deferred_duplicate_on_deep_page_holds_cursor(store):
    adapter = FakeGreen({('AWS', 1): [job(1), job(2)],
                         ('AWS', 2): [job(2)], ('AWS', 3): [job(3)]})
    _, stats = coverage(adapter, store, max_model_jobs=1)
    assert adapter.pages == [('AWS', 1), ('AWS', 2)]
    assert adapter.reads == [job(1).job_id, job(2).job_id]
    assert stats['deferred'] == 1
    assert stats['cursors']['AWS'] == {'before': 2, 'after': 2}


def test_default_depth_wrap_and_partial_success(store):
    store.advance_source('AWS', 14)
    adapter = FakeGreen(dataset())
    _, stats = coverage(adapter, store)
    assert adapter.pages == [('AWS', 1), ('AWS', 14), ('AWS', 15)]
    assert stats['cursors']['AWS'] == {'before': 14, 'after': 2}
    class Broken(FakeGreen):
        def search_cards(self, keyword, page):
            if page == 3:
                raise RuntimeError('fictional failure')
            return super().search_cards(keyword, page)
    with pytest.raises(RuntimeError):
        coverage(Broken(dataset()), store)
    assert store.source_cursor('AWS', 15) == 3


def test_duplicate_deferred_card_does_not_save_incomplete_detail(store):
    adapter = FakeGreen({('AWS', 1): [job(1), job(2)], ('AWS', 2): [job(2)]})
    _, stats = coverage(adapter, store, max_jobs=1)
    assert stats['deferred'] == 1
    assert store.existing_job(job(2).job_id) is None
    assert adapter.pages == [('AWS', 1), ('AWS', 2)]
    assert stats['cursors']['AWS'] == {'before': 2, 'after': 2}


def test_cli_outputs_actual_source_pages(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from scout_agent.search import search_command, SearchCodex

    adapter = FakeGreen(dataset())
    adapter.ensure_verified = lambda: None
    page = Mock()

    @contextmanager
    def fake_open(self):
        yield SimpleNamespace(contexts=[SimpleNamespace(new_page=lambda: page)])

    monkeypatch.setattr('scout_agent.browser.manager.BrowserManager.open', fake_open)
    monkeypatch.setattr(SearchCodex, 'classify_jobs',
                        lambda self, jobs: FakeCodex().classify_jobs(jobs))
    settings = SimpleNamespace(browser_mode='cdp', profile_path=tmp_path / 'unused',
        cdp_endpoint='http://fictional.invalid', db_path=tmp_path / 'fictional.db',
        output_path=tmp_path / 'fictional-reports', codex_model='fictional', codex_batch_size=8)
    args = SimpleNamespace(keyword=['AWS'], max_jobs=30, max_model_jobs=20,
                           pages_per_keyword=None, coverage_pages=2, max_depth=15)
    for pages, cursor in [('1,2,3', '2 → 4'), ('1,4,5', '4 → 6')]:
        assert search_command(args, settings, adapter=adapter) == 0
        output = capsys.readouterr()
        assert f'AWS pages: {pages}' in output.out
        assert f'AWS cursor: {cursor}' in output.out
        assert output.err == ''
