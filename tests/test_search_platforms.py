"""Fictional offline contract and legacy compatibility evidence."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from scout_agent.green_discovery import GreenSearchAdapter, Job
from scout_agent.search import POLICY_VERSION, SearchEvaluation, SearchStore, content_hash, run_search
from scout_agent.search_platforms import job_identity, source_identity
from scout_agent.storage.db import Database


def test_identity_and_cursor_namespaces():
    assert job_identity('green', '900001:1') == '900001:1'
    assert job_identity('type', '900001:1') != job_identity('green', '900001:1')
    assert job_identity('type', '1') != job_identity('doda', '1')
    assert source_identity('green', 'AWS') == 'AWS'
    assert source_identity('type', 'AWS') != source_identity('doda', 'AWS')


@pytest.mark.parametrize('platform,external', [('type:x', '1'), ('type', ''), ('green', 'x'), ('type', '1/2')])
def test_invalid_identity_rejected(platform, external):
    with pytest.raises(ValueError):
        job_identity(platform, external)


def test_green_contract_rejects_identity_url_mismatch():
    adapter = GreenSearchAdapter()
    job = Job.from_url('/company/900001/job/1', {})
    assert adapter.platform_key == job.platform == 'green'
    assert adapter.validate_job(job) == job.url
    job.job_id = '900001:2'
    with pytest.raises(ValueError):
        adapter.validate_job(job)


@pytest.mark.parametrize('status', ['APPLIED', 'EXCLUDED'])
def test_legacy_read_migration_cache_and_user_state(tmp_path, status):
    conn = sqlite3.connect(tmp_path / 'fictional.db')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
    CREATE TABLE search_jobs(job_id TEXT PRIMARY KEY, url TEXT, payload TEXT, keywords TEXT);
    CREATE TABLE search_evaluations(job_id TEXT,content_hash TEXT,policy_version TEXT,payload TEXT,provider TEXT,
      PRIMARY KEY(job_id,content_hash,policy_version));
    CREATE TABLE search_job_user_state(job_id TEXT PRIMARY KEY,status TEXT,updated_at TEXT);
    ''')
    job = Job.from_url('/company/900001/job/1', {'title': 'SRE', 'responsibilities': '虚构 Cloud 基盘职责'})
    result = SearchEvaluation(verdict='POSSIBLE', summary='虚构职位待人工确认。')
    conn.execute('INSERT INTO search_jobs VALUES(?,?,?,?)', (job.job_id,job.url,json.dumps(job.fields),'["AWS"]'))
    conn.execute('INSERT INTO search_evaluations VALUES(?,?,?,?,?)', (job.job_id,content_hash(job.fields),POLICY_VERSION,result.model_dump_json(),'fictional'))
    conn.execute('INSERT INTO search_job_user_state VALUES(?,?,?)', (job.job_id,status,'fictional-time'))
    conn.commit()
    db = SimpleNamespace(conn=conn)
    before = conn.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    assert SearchStore(db, migrate=False).current_results()[0][1] == result
    assert conn.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == before
    store = SearchStore(db)
    assert store.existing_job(job.job_id).platform is None
    assert store.cached(job, POLICY_VERSION) == result
    assert store.user_states()[job.job_id] == status
    store.save_job(job)
    SearchStore(db)
    assert store.user_states()[job.job_id] == status
    assert store.cached(job, POLICY_VERSION) == result
    assert store.existing_job(job.job_id).platform == 'green'
    conn.close()


def test_funnel_uses_platform_cursor_and_preserves_green_cursor(tmp_path):
    class FictionalAdapter:
        platform_key = 'fictional'
        def search_cards(self, keyword, page):
            pages.append(page)
            return []
        def job_detail(self, job):
            raise AssertionError('no detail expected')
    pages = []
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        store.advance_source('AWS', 7)
        store.advance_source('fictional:AWS', 4)
        run_search(FictionalAdapter(), store, None, keywords=['AWS'], pages_per_keyword=None)
        assert pages == [1]
        assert store.source_cursor('AWS', 15) == 7
        assert store.source_cursor('fictional:AWS', 15) == 2
