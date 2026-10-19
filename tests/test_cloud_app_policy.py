"""TASK-015 fictional duties only; no browser, site or paid model access."""
from urllib.parse import parse_qs, urlsplit

import pytest

from scout_agent.cloud_app_policy import client_site_conflict, confirmed_inhouse_workplace
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
    ('Backend Engineer', '自社製品の AWS Lambda/API Gateway/DynamoDB サービスを開発。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Full-stack Engineer', '自社サービスを ECS / AWS 托管サービスで実装・デプロイ。自社オフィス勤務。', 'TARGET_CONFIRMED', 'KEEP'),
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
    ('Backend Engineer', '自社製品の AWS Lambda/API Gateway を開発。自社オフィス勤務。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', '会社は自社サービスを提供。本募集の勤務地は入社後決定。AWS Lambda を開発。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', '自社オフィス勤務。必須スキル AWS、通常のアプリ開発を担当。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', '自社オフィス勤務。必須スキル AWS。通常のアプリ開発を担当。', 'TARGET_UNCERTAIN', 'MAYBE'),
    ('Backend Engineer', '当社では客先常駐を含むSES事業も運営。本募集は自社サービスのAWS Lambda開発で自社オフィス勤務。', 'TARGET_CONFIRMED', 'KEEP'),
    ('Backend Engineer', '客先常駐のない自社製品開発です。AWS Lambda を実装。', 'TARGET_CONFIRMED', 'KEEP'),
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
    ('当社では客先常駐を含むSES事業も運営。本募集は自社サービスのAWS Lambda開発で自社オフィス勤務。', False),
    ('客先常駐のない自社製品開発です。', False),
    ('客先常駐なし。SESなし。', False),
    ('客先常駐はありません。', False),
    ('会社はSES事業。本職は自社製品を自社内で開発。', False),
    ('自社オフィスで受託開発。派遣ではありません。', False),
    ('客先常駐なしの案件もある。本職は客先常駐で勤務。', True),
    ('SESなし。本職は派遣先勤務。', True),
    ('仕事内容：客先常駐で AWS 開発（SES事業）。', True),
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
@pytest.mark.parametrize('assignment', [
    '客先常駐なしの案件もあるが、本募集は客先常駐で勤務。',
    'SESなし。本職は派遣先勤務。', 'SES案件に配属。', '案件に応じて客先勤務。',
])
def test_local_client_assignment_overrides_cloud_app(platform, assignment):
    scout = Scout(platform=platform, job_title='AWS Backend Engineer',
                  jd_text='自社製品の AWS Lambda を開発。' + assignment)
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


@pytest.mark.parametrize('platform', ['type', 'doda', 'mynavi'])
@pytest.mark.parametrize('context', [
    '当社では客先常駐を含むSES事業も運営。',
    '客先常駐のない自社製品開発です。',
    '当社にはSES事業もある。受託開発を担当。派遣ではありません。',
    '会社紹介：SES事業では案件選択制、単価連動、還元率80。',
])
@pytest.mark.parametrize('structured', [False, True])
def test_inhouse_assignment_survives_platform_hard_gates(platform, context, structured):
    duties = context + '本募集は自社オフィス勤務で AWS Lambda API を開発。'
    scout = Scout(platform=platform, job_title='Backend Engineer',
                  jd_text=('jobContentOutline: ' if structured else '') + duties)
    assert not detail_drop({'responsibilities': duties})
    assert MockClassifier().classify(scout).verdict == 'KEEP'
    assert reprocess_stored_scout(scout).decision == 'classifier_candidate'


def test_web_indeed_sources_use_adapter_source_of_truth(tmp_path):
    from fastapi.testclient import TestClient
    from scout_agent.web.app import create_app
    with TestClient(create_app(tmp_path / 'fictional.db')) as client:
        response = client.get('/search')
    assert response.status_code == 200
    labels = response.context['source_labels']['indeed']
    assert tuple(labels) == SOURCES
    assert 'Object.keys(labels)' in response.text
    assert 'renderSources, platformSources' in response.text


@pytest.mark.parametrize('location', ['自社オフィス勤務。勤務地は入社後決定。', '勤務地・配属は未定。'])
def test_location_field_uncertainty_overrides_product_and_office(location):
    scout = Scout(platform='fictional', job_title='Backend Engineer',
                  jd_text='自社製品の AWS Lambda を開発。自社オフィス勤務。',
                  location_text=location)
    assert assess_target_domain(scout).status == 'TARGET_UNCERTAIN'
    assert MockClassifier().classify(scout).verdict == 'MAYBE'


@pytest.mark.parametrize('assignment,conflict,status', [
    ('派遣ではありません。勤務先は案件により決定。', False, 'TARGET_UNCERTAIN'),
    ('非派遣・プロジェクト先勤務の場合あり。', False, 'TARGET_UNCERTAIN'),
    ('非派遣。', False, 'TARGET_UNCERTAIN'),
    ('派遣ではありません。', False, 'TARGET_UNCERTAIN'),
    ('自社オフィス勤務。勤務先は案件により決定。', False, 'TARGET_UNCERTAIN'),
    ('自社オフィス勤務。非派遣・プロジェクト先勤務の場合あり。', False, 'TARGET_UNCERTAIN'),
    ('自社オフィス勤務でAWS Lambdaを開発。客先常駐なし。', False, 'TARGET_CONFIRMED'),
    ('当社のSES事業では客先常駐に配属する場合もありますが、今回の採用は自社オフィス勤務です。', False, 'TARGET_CONFIRMED'),
    ('当社のSES事業では客先常駐を担当しますが、今回の採用は自社オフィス勤務です。', False, 'TARGET_CONFIRMED'),
    ('当社には自社勤務の案件もありますが、今回の採用は客先常駐に配属します。', True, 'TARGET_REJECTED'),
    ('当社のSES事業では客先常駐勤務を行っていますが、今回の採用は自社オフィス勤務です。', False, 'TARGET_CONFIRMED'),
    ('当社では客先常駐を含むSES事業も運営していますが、今回の採用は自社オフィス勤務です。', False, 'TARGET_CONFIRMED'),
    ('客先常駐を行わない自社プロダクト開発です。', False, 'TARGET_UNCERTAIN'),
    ('客先常駐は原則ありません。', False, 'TARGET_UNCERTAIN'),
    ('客先常駐のない自社製品開発です。', False, 'TARGET_CONFIRMED'),
    ('SESなし。本職は派遣先勤務。', True, 'TARGET_REJECTED'),
    ('本募集は客先常駐勤務。', True, 'TARGET_REJECTED'),
    ('SES案件に配属。', True, 'TARGET_REJECTED'),
    ('客先常駐なしの案件もあるが本募集は客先常駐で勤務。', True, 'TARGET_REJECTED'),
    ('派遣先勤務。', True, 'TARGET_REJECTED'),
    ('客先常駐を行わない案件もあるが本職は派遣先勤務。', True, 'TARGET_REJECTED'),
    ('客先常駐は原則ありません。本募集は客先常駐勤務。', True, 'TARGET_REJECTED'),
])
@pytest.mark.parametrize('platform', ['type', 'doda', 'mynavi'])
def test_client_site_clause_scope_through_real_entrypoints(platform, assignment, conflict, status):
    # All text is fictional. Product ownership alone must not confirm workplace.
    duties = assignment + '自社製品の AWS Lambda/API Gateway を開発。'
    scout = Scout(platform=platform, job_title='Backend Engineer', jd_text=duties)
    assert client_site_conflict(assignment) is conflict
    assert bool(detail_drop({'responsibilities': duties})) is conflict
    assert assess_target_domain(scout).status == status
    result = MockClassifier().classify(scout)
    assert result.verdict == {'TARGET_CONFIRMED': 'KEEP', 'TARGET_UNCERTAIN': 'MAYBE',
                              'TARGET_REJECTED': 'SKIP'}[status]
    assert result.client_site is (True if conflict else None)
    local = reprocess_stored_scout(scout)
    assert local.decision == ('local_skip' if conflict else 'classifier_candidate')
    if conflict:
        assert local.evaluation.verdict == 'SKIP'


@pytest.mark.parametrize('workplace', [
    '非派遣', '派遣ではありません', '派遣ではない',
    '自社オフィス勤務。勤務先は案件により決定。',
    '自社オフィス勤務。非派遣・プロジェクト先勤務の場合あり。',
])
def test_non_dispatch_is_not_workplace_confirmation(workplace):
    assert not confirmed_inhouse_workplace(workplace)
