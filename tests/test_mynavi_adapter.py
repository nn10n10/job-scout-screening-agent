"""Fictional マイナビ fixtures; no real Chrome, Scout text, or paid model."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.local_reprocess import reprocess_stored_scout
from scout_agent.models.scout import Scout
from scout_agent.platforms.mynavi import (
    MynaviAdapter, MynaviScoutRef, mynavi_detail_prefilter,
    mynavi_external_id, mynavi_primary_target_duty, parse_mynavi_ref,
)
from scout_agent.storage.db import Database


def test_mynavi_ref_uses_delivery_and_independent_job_id():
    first = parse_mynavi_ref(
        "/jobinfo-123456-5-17-1/?matchKbn=2&msgKbn=7&deliveryId=40&cs=fictional",
        checkbox_key="123456-5-17-40", company_name="架空基盤株式会社",
        scout_title="AWS エンジニア募集のご案内", received_label="受信日：2026/09/28",
    )
    second = parse_mynavi_ref(
        "/jobinfo-123456-5-17-2/?matchKbn=2&msgKbn=7&deliveryId=40",
        checkbox_key=None, company_name="架空基盤株式会社",
        scout_title="別の職種もご案内", received_label="受信日：2026/09/28",
    )
    assert first.external_id == "delivery:40:job:123456-5-17-1"
    assert second.external_id == "delivery:40:job:123456-5-17-2"
    assert first.external_id != second.external_id
    assert first.job_title is None  # Message subject is not a job title.
    assert first.received_on == date(2026, 9, 28)
    assert first.scout_kind == "corporate_scout"


@pytest.mark.parametrize("href,key", [
    ("https://other.invalid/jobinfo-123456-5-17-1/?deliveryId=40", None),
    ("/jobinfo-123456-5-17-1/?deliveryId=not-id", None),
    ("/jobinfo-123456-5-17-1/", None),
    ("/jobinfo-123456-5-17-1/?deliveryId=40", "123456-5-17-41"),
    ("/jobinfo-123456-5-17-1/?deliveryId=40&deliveryId=41", None),
])
def test_mynavi_ref_rejects_unsafe_or_unstable_links(href, key):
    with pytest.raises(ValueError):
        parse_mynavi_ref(
            href, checkbox_key=key, company_name=None, scout_title=None,
            received_label=None,
        )


def test_mynavi_detail_prefilter_keeps_target_and_ambiguous_roles():
    cloud = Scout(
        platform="mynavi", job_title="Cloud Infrastructure Engineer",
        jd_text="jobContentOutline: AWS 基盤を設計・構築。\n"
                "jobContentDetail: Web 開発チームを支援。",
    )
    assert mynavi_detail_prefilter(cloud) is None
    generic = Scout(platform="mynavi", job_title="ITエンジニア",
                    jd_text="jobContentOutline: 仕事内容は配属後に決定。")
    assert mynavi_detail_prefilter(generic) is None
    app = Scout(platform="mynavi", job_title="アプリケーションエンジニア",
                jd_text="jobContentOutline: Web アプリの開発を担当。")
    assert "Application/Web" in mynavi_detail_prefilter(app)


@pytest.mark.parametrize("title,outline,category", [
    ("エンジニア／多要素認証SaaS開発",
     "クラウド認証システムの開発／運用を担当。", "SaaS/Application"),
    ("クラウドサービス開発エンジニア",
     "クラウドサービスの企画・開発が主な業務。", "SaaS/Application"),
    ("ITエンジニア", "Webアプリ開発やクラウド構築を担当。", "Application/Web"),
])
def test_mynavi_cloud_product_development_is_not_infrastructure(title, outline, category):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}\n"
                          "jobContentDetail: フロントエンドとバックエンドの開発を担当。")
    assert not mynavi_primary_target_duty(scout.jd_text)
    result = reprocess_stored_scout(scout)
    assert result.decision == "local_skip"
    assert result.model_name == "mynavi-detail-prefilter"
    assert category in result.reason


@pytest.mark.parametrize("title,outline", [
    ("クラウドエンジニア", "AWS 基盤の設計・構築を主担当。"),
    ("インフラエンジニア", "クラウド環境の設計・運用が主要業務。"),
    ("ITエンジニア", "アプリ開発とクラウド基盤の構築をそれぞれ担当。"),
])
def test_mynavi_explicit_cloud_infrastructure_remains_candidate(title, outline):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}")
    assert mynavi_primary_target_duty(scout.jd_text)
    assert reprocess_stored_scout(scout).decision == "classifier_candidate"


def test_mynavi_ses_rule_requires_combined_evidence_and_protects_inhouse():
    target = Scout(
        platform="mynavi", job_title="インフラエンジニア",
        jd_text="jobContentOutline: AWS 基盤の設計・構築を担当。\n"
                "jobContentDetail: 待機期間も給与100％保証。",
        location_text="首都圏のプロジェクト先に配属。",
    )
    result = reprocess_stored_scout(target)
    assert result.decision == "local_skip"
    assert result.model_name == "mynavi-ses-hard-rule"
    assert "客先/项目现场配属" in result.reason
    pool = Scout(
        platform="mynavi", job_title="ITエンジニア",
        jd_text="jobContentOutline: 担当職種は配属後に決定。\n"
                "jobContentDetail: 案件リストから希望案件を選択。還元率80％。",
    )
    assert reprocess_stored_scout(pool).model_name == "mynavi-ses-hard-rule"
    assert reprocess_stored_scout(pool.model_copy(update={
        "jd_text": "jobContentOutline: 担当職種は配属後に決定。\n"
                   "jobContentDetail: 案件リストから希望案件を選択。",
    })).decision == "classifier_candidate"
    assert reprocess_stored_scout(pool.model_copy(update={
        "jd_text": "jobContentOutline: 担当職種は配属後に決定。\n"
                   "jobContentDetail: 受託・直請けのチーム案件を担当。",
    })).decision == "classifier_candidate"
    assert reprocess_stored_scout(pool.model_copy(update={
        "jd_text": "jobContentOutline: 担当職種は配属後に決定。\n"
                   "jobContentDetail: 会社都合のアサインから脱却したい方を歓迎。"
                   "待機中でも給与の変更はあります。",
    })).decision == "classifier_candidate"
    assert reprocess_stored_scout(pool.model_copy(update={
        "location_text": "自社内勤務。客先常駐なし。",
    })).decision == "classifier_candidate"


@pytest.mark.parametrize("title,outline,category", [
    ("受付コンシェルジュ", "ご来店されたお客様の相談を受け、接客・案内を担当。", "受付・接客"),
    ("お部屋探しサポート", "お客様の希望に沿う住まい探しをサポート。", "住まい探し"),
    ("店舗カウンター接客", "店頭でお客様に商品をご案内。", "店舗・カウンター"),
])
def test_mynavi_detail_prefilter_skips_evidenced_non_it_service_duties(
    title, outline, category,
):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}\n"
                          "jobContentDetail: 顧客への対面サービスを担当。")
    result = reprocess_stored_scout(scout)
    assert result.decision == "local_skip"
    assert category in result.reason


@pytest.mark.parametrize("title,outline", [
    ("受付コンシェルジュ", "仕事内容は配属後に決定。"),
    ("お部屋探しサポート", "仕事内容は配属後に決定。"),
    ("クラウドエンジニア（店舗接客システム）", "AWS 基盤の設計・構築を主担当。"),
])
def test_mynavi_service_terms_do_not_skip_without_non_it_primary_duty(title, outline):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}")
    assert reprocess_stored_scout(scout).decision == "classifier_candidate"


def test_mynavi_primary_infra_duty_overrides_non_target_title_fallback():
    infrastructure_pmo = Scout(
        platform="mynavi", job_title="プロジェクト管理【PMO】",
        jd_text="jobContentOutline: AWS インフラ基盤の設計・構築を主担当。",
    )
    assert reprocess_stored_scout(infrastructure_pmo).decision == "classifier_candidate"
    ordinary_pmo = infrastructure_pmo.model_copy(update={
        "jd_text": "jobContentOutline: 顧客向け業務システムの進捗管理。",
    })
    assert reprocess_stored_scout(ordinary_pmo).decision == "local_skip"


@pytest.mark.parametrize("title,outline,category", [
    ("ITエンジニア", "顧客向け業務システムの設計・開発を担当。", "System/Package"),
    ("社内SE", "社内 Web アプリケーションの機能開発を担当。", "Application/Web"),
    ("ERP／CRMエンジニア", "Dynamics 365 の導入支援を担当。", "ERP/CRM/SAP"),
    ("BIエンジニア", "ダッシュボードの設計・開発を担当。", "BI/数据分析"),
    ("QAエンジニア", "ソフトウェアテストと品質保証を担当。", "QA/Test"),
    ("プロジェクト管理【PMO】", "業務システムの進捗管理を担当。", "System/Package"),
    ("ITコンサルタント", "顧客の IT 戦略の企画を担当。", "IT Consulting"),
    ("フロントエンドエンジニア", "AWS 環境を用いた Web アプリ開発を担当。", "Application/Web"),
    ("AWSエンジニア", "医療向けサービスの Backend 開発を担当。", "Backend/Frontend"),
    ("ITエンジニア", "水道・ガス等の都市インフラ管理システムの開発を担当。", "社会基础设施"),
    ("アプリエンジニア", "スマホアプリの開発を担当。", "Mobile/Embedded"),
])
def test_mynavi_detail_prefilter_skips_evidenced_non_target_main_duties(
    title, outline, category,
):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}\n"
                          "jobContentDetail: AWS や Terraform の使用経験がある方を歓迎。")
    result = reprocess_stored_scout(scout)
    assert result.decision == "local_skip"
    assert result.model_name == "mynavi-detail-prefilter"
    assert category in result.reason


@pytest.mark.parametrize("title,outline", [
    ("クラウドエンジニア", "AWS 基盤の設計・構築を主担当。"),
    ("インフラエンジニア", "ネットワークの設計・構築・運用を担当。"),
    ("プラットフォームエンジニア（バックエンド）", "Kubernetes 環境の最適化と CI/CD 共通基盤の構築を担当。"),
    ("PMO", "AWS インフラ基盤の設計・構築を主担当。"),
    ("ITエンジニア", "アプリ開発とクラウド基盤の構築をそれぞれ担当。"),
    ("社内SE", "配属後に担当業務を決定。"),
])
def test_mynavi_detail_prefilter_keeps_target_or_unresolved_main_duties(title, outline):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}")
    assert reprocess_stored_scout(scout).decision == "classifier_candidate"


def test_mynavi_platform_engineering_body_overrides_backend_title():
    scout = Scout(
        platform="mynavi", job_title="プラットフォームエンジニア（バックエンド）",
        jd_text="jobContentOutline: 開発とインフラの知見を活かして標準化を推進。\n"
                "jobContentDetail: Platform Engineering\n"
                "オンプレKubernetes環境のリソース最適化を担当。",
    )
    assert reprocess_stored_scout(scout).decision == "classifier_candidate"


@pytest.mark.parametrize("title,outline", [
    ("AWSエンジニア", "クラウド型業務システムの Backend を設計・開発します。"),
    ("プロジェクト統括【PMO】", "インフラ構築プロジェクトで PMO として進捗管理を担当。"),
    ("ITエンジニア", "社会インフラ管理システムの設計・開発を担当。"),
    ("開発エンジニア", "希望する案件に参画し、要件定義・設計・開発・テストを担当。"),
    ("ソフトウェアエンジニア", "自社製品の開発全工程を担当。"),
])
def test_mynavi_does_not_treat_domain_or_incidental_cloud_as_primary_infra(title, outline):
    scout = Scout(platform="mynavi", job_title=title,
                  jd_text=f"jobContentOutline: {outline}\n"
                          "jobContentDetail: 開発環境には AWS と Terraform を利用。")
    assert reprocess_stored_scout(scout).decision == "local_skip"


def _ref(index: int, *, job_title: str | None = None, age: int = 1) -> MynaviScoutRef:
    job_id = f"12345{index}-5-17-1"
    delivery_id = str(40 + index)
    return MynaviScoutRef(
        external_id=mynavi_external_id(delivery_id, job_id), job_id=job_id,
        delivery_id=delivery_id,
        url=f"https://tenshoku.mynavi.jp/jobinfo-{job_id}/?deliveryId={delivery_id}",
        company_name=f"架空会社{index}", scout_title="架空 Scout のご案内",
        received_on=datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=age),
        job_title=job_title,
    )


class FakePage:
    def __init__(self):
        self.url = ""
        self.closed = False

    def goto(self, url, **_kwargs):
        self.url = url

    def get_by_role(self, role, *, name):
        assert role == "heading" and name == "企業からのスカウト受信一覧"
        return SimpleNamespace(wait_for=lambda **_kwargs: None)

    def close(self):
        self.closed = True


class FakeMynaviAdapter:
    scout_list_url = MynaviAdapter.scout_list_url
    refs: list[MynaviScoutRef] = []
    detail_titles: dict[str, str] = {}
    detail_calls: list[str] = []

    def is_logged_in(self, page):
        return page.url == self.scout_list_url

    def iter_scout_list(self, _page, *, limit=100):
        yield from self.refs[:limit]

    def get_scout_detail(self, _page, ref, *, max_age_days=14):
        self.detail_calls.append(ref.external_id)
        if (ref.received_on is None or
                (datetime.now(ZoneInfo("Asia/Tokyo")).date() - ref.received_on).days > max_age_days):
            return []
        title = self.detail_titles.get(ref.external_id, "Cloud Infrastructure Engineer")
        outline = ("Web アプリの開発を担当。" if "アプリ" in title
                   else "AWS 基盤の設計・構築を担当。")
        return [{
            "id": ref.external_id, "platform": "mynavi",
            "company_name": ref.company_name, "job_title": title,
            "scout_title": ref.scout_title, "scout_text": "架空のご案内",
            "jd_text": f"jobContentOutline: {outline}",
            "salary_text": "年収500万円", "location_text": "東京",
            "url": ref.url, "received_on": ref.received_on,
            "scout_kind": "corporate_scout", "sender_kind": "company",
            "is_bulk_like": None,
        }]

    def normalize_scout(self, raw):
        return Scout.model_validate(raw)


def _setup(tmp_path, monkeypatch, refs, titles=None):
    FakeMynaviAdapter.refs = refs
    FakeMynaviAdapter.detail_titles = titles or {}
    FakeMynaviAdapter.detail_calls = []
    monkeypatch.setitem(cli.ADAPTERS, "mynavi", FakeMynaviAdapter)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(
        tmp_path, None, None, classifier_provider="mock",
    ))
    page = FakePage()

    class FakeManager:
        def __init__(self, *_args, **kwargs):
            assert kwargs["mode"] == "cdp"

        @contextmanager
        def open(self):
            yield SimpleNamespace(contexts=(SimpleNamespace(new_page=lambda: page),))

    monkeypatch.setattr(cli, "BrowserManager", FakeManager)
    return page


def test_mynavi_two_stage_scan_audit_local_skip_and_zero_repeat(tmp_path, monkeypatch, capsys):
    title_skip = _ref(1, job_title="法人営業")
    local_skip = _ref(2)
    candidate = _ref(3)
    page = _setup(tmp_path, monkeypatch, [title_skip, local_skip, candidate], {
        local_skip.external_id: "アプリケーションエンジニア",
        candidate.external_id: "Cloud Infrastructure Engineer",
    })
    assert cli.main(["scan", "--platform", "mynavi"]) == 0
    assert FakeMynaviAdapter.detail_calls == [local_skip.external_id, candidate.external_id]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.mynavi_list_status(title_skip.external_id) == "title_skip"
        assert db.count_processed() == 2
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        rows = db.get_scan_audit(run_id)
        assert [(row["decision"], row["detail_fetched"]) for row in rows] == [
            ("TITLE_SKIP", 0), ("DETAIL", 1), ("DETAIL", 1),
        ]
        assert rows[1]["detail_decision"] == "DETAIL_LOCAL_SKIP"
        assert rows[1]["job_title"] == "アプリケーションエンジニア"
        assert rows[1]["list_title"] == "架空 Scout のご案内"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM local_reprocess WHERE decision='classifier_candidate'"
        ).fetchone()[0] == 0  # Successful classification consumes eligibility.
        assert {row[0] for row in db.conn.execute("SELECT DISTINCT provider FROM evaluations")} == {
            "local", "mock",
        }
    out = capsys.readouterr().out
    assert "TITLE_SKIP: 1" in out
    assert "DETAIL_LOCAL_SKIP: 1" in out
    assert "sent_to_classifier: 1" in out
    monkeypatch.setattr(cli, "_classifier", lambda _: (_ for _ in ()).throw(
        AssertionError("seen Scouts must consume zero model tokens")
    ))
    assert cli.main(["scan", "--platform", "mynavi"]) == 0
    assert FakeMynaviAdapter.detail_calls == [local_skip.external_id, candidate.external_id]
    assert "New scouts: 0" in capsys.readouterr().out
    assert page.closed


def test_mynavi_age_limit_and_seen_threshold(tmp_path, monkeypatch, capsys):
    refs = [_ref(index) for index in range(31)]
    old = _ref(90, age=20)
    _setup(tmp_path, monkeypatch, [*refs, old])
    with Database(tmp_path / "data" / "scouts.db") as db:
        for ref in refs[:30]:
            db.save_mynavi_list_item(
                external_id=ref.external_id, job_id=ref.job_id,
                delivery_id=ref.delivery_id, company_name=ref.company_name,
                scout_title=ref.scout_title,
                received_on=ref.received_on.isoformat(), url=ref.url,
                status="completed",
            )
    assert cli.main(["scan", "--platform", "mynavi"]) == 0
    assert FakeMynaviAdapter.detail_calls == []
    assert "list_scanned: 30" in capsys.readouterr().out
    # A pending item suppresses the seen threshold so interrupted scans resume.
    with Database(tmp_path / "data" / "scouts.db") as db:
        db.save_mynavi_list_item(
            external_id=refs[30].external_id, job_id=refs[30].job_id,
            delivery_id=refs[30].delivery_id, company_name=refs[30].company_name,
            scout_title=refs[30].scout_title,
            received_on=refs[30].received_on.isoformat(), url=refs[30].url,
            status="pending",
        )
    assert cli.main(["scan", "--platform", "mynavi"]) == 0
    assert FakeMynaviAdapter.detail_calls == [refs[30].external_id]
    assert "too_old: 1" in capsys.readouterr().out
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.mynavi_list_status(old.external_id) == "too_old"


def test_mynavi_detail_timeout_is_audited_and_left_pending(tmp_path, monkeypatch):
    slow, ready = _ref(1), _ref(2)
    _setup(tmp_path, monkeypatch, [slow, ready])
    original = FakeMynaviAdapter.get_scout_detail

    def one_timeout(self, page, ref, *, max_age_days=14):
        if ref.external_id == slow.external_id:
            raise PlaywrightTimeoutError("fictional navigation timeout")
        return original(self, page, ref, max_age_days=max_age_days)

    monkeypatch.setattr(FakeMynaviAdapter, "get_scout_detail", one_timeout)
    assert cli.main(["scan", "--platform", "mynavi"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        audit = db.get_scan_audit(run_id)
        assert audit[0]["detail_decision"] == "DETAIL_ERROR"
        assert audit[0]["detail_fetched"] == 0
        assert "TimeoutError" in audit[0]["detail_reason"]
        assert db.mynavi_list_status(slow.external_id) == "pending"
        assert db.mynavi_list_status(ready.external_id) == "completed"
        assert db.count_processed() == 1
