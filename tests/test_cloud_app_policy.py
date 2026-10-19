"""TASK-015 fictional duties only; no browser, site or paid model access."""
from urllib.parse import parse_qs, urlsplit

import pytest

from scout_agent.cloud_app_policy import client_site_conflict
from scout_agent.indeed_search import IndeedSearchAdapter, SOURCES
from scout_agent.llm.mock import MockClassifier
from scout_agent.local_reprocess import reprocess_stored_scout
from scout_agent.models.scout import Scout
from scout_agent.search import (POLICY_VERSION, SearchStore, SearchEvaluation,
                                list_drop, detail_drop, run_search)
from scout_agent.search_platforms import Job
from scout_agent.storage.db import Database
from scout_agent.target_domain import assess_target_domain


@pytest.mark.parametrize('title,duties,status,verdict', [
    ('Backend Engineer', '自社製品の AWS Lambda/API Gateway/DynamoDB サービスを開発。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Full-stack Engineer', '自社サービスを ECS / AWS 托管サービスで実装・デプロイ。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Server-side Engineer', '自社オフィスで AWS 上の API を開発。', 'TARGET_CONFIRMED', 'KEEP'),
    ('SRE', 'AWS 基盤の設計・構築を主担当。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', 'API 開発。\n技術スタック\nAWS Python', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', 'AWS Python。担当業務は面談で説明。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', 'オンプレのみの非クラウド業務システム開発。', 'TARGET_REJECTED', 'SKIP'),
    ('Backend Engineer', '客先常駐で AWS Lambda サービスを開発。', 'TARGET_REJECTED', 'SKIP'),
    ('Backend Engineer', '客先常駐なし。自社サービスの AWS Lambda を開発。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', '会社紹介：SES事業もあります。自社製品を自社オフィスで AWS 上に実装。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', '非派遣の受託開発。自社オフィスで AWS Lambda サービスを開発。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', 'AWS Lambda サービスを開発。勤務地・配属は未定。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', '自社製品の AWS Lambda を開発。勤務地・配属は未定。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', '', 'TARGET_UNCERTAIN', 'MAYBE'),
])
def test_scout_policy(title, duties, status, verdict):
    scout = Scout(platform='fictional', job_title=title, jd_text=duties)
    assessment = assess_target_domain(scout)
    assert assessment.status == status
    result = MockClassifier().classify(scout)
    assert result.verdict == verdict
    if assessment.category == 'CLOUD_APP_DEV' and status == 'TARGET_CONFIRMED':
        assert assessment.evidence and assessment.evidence in duties
    if 'なし' in duties:
        assert result.client_site is None


@pytest.mark.parametrize('platform', ['type', 'doda', 'mynavi'])
@pytest.mark.parametrize('duties', [
    '自社製品の AWS Lambda/API Gateway/DynamoDB サービスを開発。',
    '自社サービスを ECS 上で実装しデプロイ。',
    'Backend 開発。AWS 等の技術を使用。配属未定。',
    '職務内容は面談で説明。',
])
def test_local_reprocess_retains_cloud_development(platform, duties):
    scout = Scout(platform=platform, job_title='Full-stack Backend Engineer',
                  jd_text='jobContentOutline: ' + duties)
    assert reprocess_stored_scout(scout).decision == 'classifier_candidate'


@pytest.mark.parametrize('text,conflict', [
    ('客先常駐なし。SESなし。', False),
    ('客先常駐はありません。', False),
    ('会社はSES事業。本職は自社製品を自社内で開発。', False),
    ('自社オフィスで受託開発。派遣ではありません。', False),
    ('客先常駐なしの案件もある。本職は客先常駐で勤務。', True),
    ('SESなし。本職は派遣先勤務。', True),
    ('SES案件に配属。AWS 開発。', True),
    ('案件に応じて客先で勤務。', True),
])
def test_explicit_site_conflict_and_negations(text, conflict):
    assert client_site_conflict(text) == conflict
    assert bool(detail_drop({'responsibilities': text})) == conflict


@pytest.mark.parametrize('title', ['Backend-only', 'Full-stack Engineer', 'Server-side Engineer', 'Frontend'])
def test_search_list_preserves_development(title):
    assert not list_drop(title)


@pytest.mark.parametrize('fields', [
    {'salary': '年収300〜449万円'},
    {'responsibilities': 'ヘルプデスクのみ'},
    {'responsibilities': '監視業務のみ'},
    {'responsibilities': 'AWS 開発。派遣先勤務。'},
])
def test_hard_exclusions_precede_cloud_match(fields):
    assert detail_drop(fields)
    scout = Scout(platform='fictional', job_title='Backend Engineer',
                  jd_text=fields.get('responsibilities', '自社製品の AWS Lambda を開発。'),
                  salary_text=fields.get('salary'))
    assert MockClassifier().classify(scout).verdict == 'SKIP'


def test_indeed_precise_sources_keep_page_one_and_identity():
    adapter = IndeedSearchAdapter()
    for source in ('AWS バックエンドエンジニア', 'クラウドネイティブ 開発', 'AWS フルスタックエンジニア'):
        assert source in SOURCES
        assert parse_qs(urlsplit(adapter.source_url(source)).query) == {'q': [source]}
        with pytest.raises(ValueError):
            adapter.source_url(source, 2)
    from scout_agent.indeed_search import job_from_url
    assert job_from_url('https://jp.indeed.com/viewjob?jk=abcdef1234567890', {}).job_id == 'indeed:abcdef1234567890'


@pytest.mark.parametrize("state", ["ACTIVE", "APPLIED", "EXCLUDED"])
def test_search_new_policy_preserves_history_and_manual_state(tmp_path, state):
    job = Job.from_url('/company/900001/job/999', {
        'title': 'Backend Engineer', 'responsibilities': '自社製品の AWS Lambda を開発。'})
    class Adapter:
        def search_cards(self, keyword, page):
            return [job] if page == 1 else []
        def job_detail(self, card):
            return card.fields
    class Model:
        calls = 0
        def classify_jobs(self, jobs):
            self.calls += 1
            return {j.job_id: SearchEvaluation(verdict='TARGET', summary='职责明确开发云服务。') for j in jobs}
    with Database(tmp_path / 'fictional.db') as db:
        store = SearchStore(db)
        model = Model()
        run_search(Adapter(), store, model, keywords=['AWS'], pages_per_keyword=1,
                   policy_version='green-search-0.1.1')
        # A new policy adds evaluation rows; old payloads are immutable.
        store.set_user_state(job.job_id, state)
        before = [tuple(r) for r in db.conn.execute('SELECT * FROM search_evaluations')]
        run_search(Adapter(), store, model, keywords=['AWS'], pages_per_keyword=1)
        assert model.calls == 2
        assert store.user_states()[job.job_id] == state
        after = [tuple(r) for r in db.conn.execute('SELECT * FROM search_evaluations')]
        assert len(after) == 2 and before[0] in after
        assert POLICY_VERSION != 'green-search-0.1.1'
        run_search(Adapter(), store, model, keywords=['AWS'], pages_per_keyword=1)
        assert model.calls == 2


@pytest.mark.parametrize('platform', ['type', 'doda', 'mynavi'])
def test_local_client_assignment_overrides_cloud_app(platform):
    scout = Scout(platform=platform, job_title='AWS Backend Engineer',
                  jd_text='自社製品の AWS Lambda を開発。SESなし。本職は派遣先勤務。')
    result = reprocess_stored_scout(scout)
    assert result.decision == 'local_skip' and result.evaluation.verdict == 'SKIP'


def test_indeed_cli_accepts_precise_source_without_browser(monkeypatch):
    import scout_agent.cli as cli
    from types import SimpleNamespace
    monkeypatch.setattr(cli, 'load_settings', lambda: SimpleNamespace())
    seen = []
    def fake_command(args, settings):
        seen.extend(args.keyword)
        return 0
    monkeypatch.setattr('scout_agent.search.search_command', fake_command)
    assert cli.main(['search', 'indeed', '--keyword', 'AWS バックエンドエンジニア']) == 0
    assert seen == ['AWS バックエンドエンジニア']
