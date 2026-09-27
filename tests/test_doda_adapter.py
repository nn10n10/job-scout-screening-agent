"""Fictional doda fixtures only; no browser, real database, or paid LLM."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.platforms.doda import (
    DodaAdapter, DodaOfferRef, doda_detail_prefilter, doda_external_id,
    parse_doda_job_payload, parse_doda_ref,
)
from scout_agent.storage.db import Database


def test_doda_ref_uses_message_and_independent_job_id():
    assert DodaAdapter.scout_list_url.endswith("/interviewOfferList/?sort_id=1")
    first = parse_doda_ref(
        "/dcfront/referredJob/interviewOfferDetail/?message_id=1234&jid=9001&wriid=77",
        card_id="1234", company_name="架空クラウド社", job_title="Cloud Engineer",
        age_label="受信日から 2 日経過", premium=True,
    )
    second = parse_doda_ref(
        "/dcfront/referredJob/offerDetail/?message_id=1234&jid=9002",
        card_id="1234", company_name="架空クラウド社", job_title="営業",
        age_label="応募期限まであと 3 日", premium=False,
    )
    assert first.external_id == "offer:1234:job:9001"
    assert second.external_id == "offer:1234:job:9002"
    assert first.age_days == 2 and second.age_days is None
    assert first.scout_kind == "premium_offer"
    assert second.scout_kind == "company_offer"


@pytest.mark.parametrize("href,card_id", [
    ("https://elsewhere.invalid/dcfront/referredJob/offerDetail/?message_id=1&jid=2", "1"),
    ("/dcfront/referredJob/offerDetail/?message_id=abc&jid=2", "abc"),
    ("/dcfront/referredJob/offerDetail/?message_id=1", "1"),
    ("/dcfront/referredJob/offerDetail/?message_id=1&jid=2", "3"),
])
def test_doda_ref_rejects_unstable_or_mismatched_links(href, card_id):
    with pytest.raises(ValueError):
        parse_doda_ref(
            href, card_id=card_id, company_name=None, job_title=None,
            age_label="", premium=False,
        )


def test_doda_job_payload_extracts_only_named_job():
    payload = {"props": {"pageProps": {"job": {"job": {
        "jid": "9001", "corporateName": "架空クラウド社",
        "occupationName": "Infrastructure Engineer",
        "recruit": {
            "jobContentOutline": "AWS 基盤構築",
            "jobContentDetail": "<p>Terraform で IaC。</p>",
            "salary": "年収 550万円", "jobState": "東京／リモート可",
        },
    }}}}}
    result = parse_doda_job_payload(payload, expected_job_id="9001")
    assert result["job_title"] == "Infrastructure Engineer"
    assert "Terraform で IaC。" in result["jd_text"]
    assert "<p>" not in result["jd_text"]
    assert result["salary_text"] == "年収 550万円"
    with pytest.raises(ValueError):
        parse_doda_job_payload(payload, expected_job_id="9002")


@pytest.mark.parametrize("title", [
    "法人営業", "一般事務", "製造スタッフ", "品質テストスタッフ", "研究職（化学・バイオ）",
    "物理シミュレーション", "次世代モビリティの開発／機械電気ソフト",
    "空港警備", "タクシードライバー", "倉庫スタッフ", "施工管理", "設備管理",
    "店舗運営・店長", "マンション管理", "eスポーツ総合職",
    "個別指導学院のPR", "総合職／ららぽーと等の運営管理",
    "人材コーディネーター", "塾講師", "Webデザイナー",
    "Webアプリケーションエンジニア", "Backend Engineer", "Frontend Engineer",
    "システム開発エンジニア", "モビリティ開発エンジニア", "組込エンジニア",
    "QA Engineer", "ソフトウェアテスト担当", "ヘルプデスク", "テクサポ",
    "キッティング担当", "IT業務支援職",
])
def test_doda_detail_prefilter_clear_non_target_roles(title):
    reason = doda_detail_prefilter(Scout(platform="doda", job_title=title, jd_text="Web アプリ開発。"))
    assert reason is not None
    assert "主要职责" in reason


@pytest.mark.parametrize("title", [
    "Cloud Engineer", "クラウド基盤エンジニア", "Infrastructure Engineer",
    "インフラエンジニア／キッティング・テクサポから上流へ", "SRE",
    "DevOps Engineer", "Platform Engineer", "AWS 基盤構築", "サーバーエンジニア",
    "ネットワークエンジニア",
])
def test_doda_detail_prefilter_preserves_target_role_even_with_ses(title):
    scout = Scout(platform="doda", job_title=title,
                  jd_text="還元率と案件選択制。客先常駐の可能性あり。")
    assert doda_detail_prefilter(scout) is None


@pytest.mark.parametrize("title", [
    "ITエンジニア", "社内SE", "情報系エンジニア", "ITサービスマネジメント",
])
def test_doda_detail_prefilter_keeps_ambiguous_it_titles(title):
    assert doda_detail_prefilter(Scout(platform="doda", job_title=title)) is None


def test_doda_detail_prefilter_requires_primary_cloud_duty_not_incidental_aws():
    incidental = Scout(
        platform="doda", job_title="Backend Engineer",
        jd_text="jobContentOutline: AWS を利用した Web アプリ開発。\n"
                "jobContentDetail: SRE チームと協業します。",
    )
    primary = incidental.model_copy(update={
        "jd_text": "jobContentOutline: AWS 基盤の設計・構築を主担当として進めます。"
    })
    assert doda_detail_prefilter(incidental) is not None
    assert doda_detail_prefilter(primary) is None


@pytest.mark.parametrize("title,outline,expected", [
    ("ITエンジニア", "業務システム開発を担当します。", "System 开发"),
    ("SE", "Webアプリ開発が主な業務です。", "Application/Web 开发"),
    ("情報系エンジニア", "社内アプリをクラウド上で開発します。", "Application/Web 开发"),
    ("情報系エンジニア", "組込ソフトウェア開発を担当。", "組込/モビリティ 开发"),
    ("ITサービスマネジメント", "ヘルプデスクとキッティングが主な業務。", "Helpdesk/技术支持"),
])
def test_doda_generic_it_skips_only_when_structured_jd_main_duty_is_clear(title, outline, expected):
    scout = Scout(platform="doda", job_title=title, jd_text=f"jobContentOutline: {outline}")
    assert expected in doda_detail_prefilter(scout)
    assert doda_detail_prefilter(scout.model_copy(update={"jd_text": "職務内容は面談で説明。"})) is None


def test_doda_client_site_non_cloud_role_skips_but_negation_does_not():
    scout = Scout(platform="doda", job_title="就業先の社内SE", jd_text="jobContentOutline: 社内IT業務。")
    assert "客先/就業先" in doda_detail_prefilter(scout)
    assert doda_detail_prefilter(scout.model_copy(update={
        "job_title": "社内SE", "jd_text": "jobContentOutline: 客先常駐なし。社内IT業務。"
    })) is None


def test_doda_mobility_title_kept_when_cloud_is_primary_duty():
    scout = Scout(
        platform="doda", job_title="ADAS・MaaS等の次世代モビリティ開発",
        jd_text="jobContentOutline: AWS クラウド基盤の設計・構築を主担当として進める。",
    )
    assert doda_detail_prefilter(scout) is None


def _ref(index: int, title: str, age: int | None = 2) -> DodaOfferRef:
    mid = str(1100000000 + index)
    jid = str(3000000000 + index)
    return DodaOfferRef(
        external_id=doda_external_id(mid, jid), message_id=mid, job_id=jid,
        url=f"https://doda.jp/dcfront/referredJob/offerDetail/?message_id={mid}&jid={jid}",
        company_name=f"架空社{index}", job_title=title, age_days=age,
        scout_kind="company_offer",
    )


class FakePage:
    def __init__(self):
        self.url = ""
        self.closed = False

    def goto(self, url, **_kwargs):
        self.url = url

    def close(self):
        self.closed = True


class FakeDodaAdapter:
    scout_list_url = "https://doda.jp/dcfront/referredJob/interviewOfferList/"
    refs: list[DodaOfferRef] = []
    detail_calls: list[str] = []

    def is_logged_in(self, page):
        return page.url == self.scout_list_url

    def iter_scout_list(self, _page, *, limit=100):
        yield from self.refs[:limit]

    def get_scout_detail(self, _page, ref, *, max_age_days=14):
        self.detail_calls.append(ref.external_id)
        if ref.age_days is None or ref.age_days > max_age_days:
            return []
        return [{
            "id": ref.external_id, "platform": "doda", "company_name": ref.company_name,
            "job_title": ref.job_title, "scout_title": ref.job_title,
            "scout_text": "架空のご案内", "jd_text": "AWS Terraform の基盤構築。",
            "salary_text": "年収 550万円", "location_text": "東京",
            "url": ref.url, "scout_kind": ref.scout_kind,
            "sender_kind": "company", "is_bulk_like": None,
            "received_at": None, "_age_days": ref.age_days,
        }]

    def normalize_scout(self, raw):
        return Scout.model_validate(raw)


def _setup(tmp_path, monkeypatch, refs):
    FakeDodaAdapter.refs = refs
    FakeDodaAdapter.detail_calls = []
    monkeypatch.setitem(cli.ADAPTERS, "doda", FakeDodaAdapter)
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


def test_doda_two_stage_scan_skips_non_target_and_dedupes_without_model(tmp_path, monkeypatch, capsys):
    skip = _ref(1, "法人営業")
    review = _ref(2, "バックエンドエンジニア")
    detail = _ref(3, "Cloud Engineer")
    page = _setup(tmp_path, monkeypatch, [skip, review, detail])
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == [review.external_id, detail.external_id]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.doda_list_status(skip.external_id) == "title_skip"
        assert db.count_processed() == 2
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        audit = db.get_scan_audit(run_id)
        assert [(row["decision"], row["detail_fetched"]) for row in audit] == [
            ("TITLE_SKIP", 0), ("TITLE_REVIEW", 1), ("DETAIL", 1),
        ]
        assert {row[0] for row in db.conn.execute("SELECT DISTINCT provider FROM evaluations")} == {"mock", "local"}
        assert audit[1]["detail_decision"] == "DETAIL_LOCAL_SKIP"
        assert "Backend" in audit[1]["detail_reason"]
        assert db.conn.execute(
            "SELECT model_name FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.external_id=?", (review.external_id,)
        ).fetchone()[0] == "doda-detail-prefilter"
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("seen doda offer must consume zero classifier tokens")
    ))
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == [review.external_id, detail.external_id]
    assert "New scouts: 0" in capsys.readouterr().out
    assert page.closed


def test_doda_old_offer_skipped_and_unknown_date_requires_detail_proof(tmp_path, monkeypatch, capsys):
    old = _ref(1, "Infrastructure Engineer", age=20)
    unknown = _ref(2, "SRE", age=None)
    _setup(tmp_path, monkeypatch, [old, unknown])
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == [unknown.external_id]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.doda_list_status(old.external_id) == "too_old"
        assert db.doda_list_status(unknown.external_id) == "too_old"
        assert db.count_processed() == 0
    out = capsys.readouterr().out
    assert "too_old: 2" in out
    assert "unknown_date: 1" in out


def test_doda_stops_after_thirty_seen_but_resumes_pending(tmp_path, monkeypatch, capsys):
    refs = [_ref(i, "Cloud Engineer") for i in range(31)]
    _setup(tmp_path, monkeypatch, refs)
    with Database(tmp_path / "data" / "scouts.db") as db:
        for ref in refs:
            db.save_doda_list_item(
                external_id=ref.external_id, company_name=ref.company_name,
                job_title=ref.job_title, age_days=ref.age_days,
                url=ref.url, status="pending" if ref == refs[-1] else "completed",
            )
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == [refs[-1].external_id]
    assert "list_scanned: 31" in capsys.readouterr().out
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == [refs[-1].external_id]
    assert "list_scanned: 30" in capsys.readouterr().out


def test_doda_extra_job_id_gets_separate_record_and_audit(tmp_path, monkeypatch):
    ref = _ref(1, "Cloud Engineer")
    _setup(tmp_path, monkeypatch, [ref])
    original = FakeDodaAdapter.get_scout_detail

    def two_jobs(self, page, item, *, max_age_days=14):
        first = original(self, page, item, max_age_days=max_age_days)[0]
        second = {**first, "id": doda_external_id(item.message_id, "3999999999"),
                  "job_title": "法人営業"}
        return [first, second]

    monkeypatch.setattr(FakeDodaAdapter, "get_scout_detail", two_jobs)
    assert cli.main(["scan", "--platform", "doda"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.count_processed() == 2
        assert db.doda_list_status(doda_external_id(ref.message_id, "3999999999")) == "completed"
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        assert [(row["external_id"], row["decision"]) for row in db.get_scan_audit(run_id)] == [
            (ref.external_id, "DETAIL"),
            (doda_external_id(ref.message_id, "3999999999"), "DETAIL"),
        ]
        assert db.get_scan_audit(run_id)[1]["detail_decision"] == "DETAIL_LOCAL_SKIP"
        assert db.get_scan_audit(run_id)[1]["list_title"] is None


def test_doda_audit_retains_prefilter_headline_after_jd_title_update(tmp_path, monkeypatch):
    ref = _ref(9, "AWS 案件のご案内")
    _setup(tmp_path, monkeypatch, [ref])
    original = FakeDodaAdapter.get_scout_detail

    def actual_title(self, page, item, *, max_age_days=14):
        row = original(self, page, item, max_age_days=max_age_days)[0]
        return [{**row, "job_title": "Cloud Infrastructure Engineer"}]

    monkeypatch.setattr(FakeDodaAdapter, "get_scout_detail", actual_title)
    assert cli.main(["scan", "--platform", "doda"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        row = db.get_scan_audit(run_id)[0]
        assert row["list_title"] == "AWS 案件のご案内"
        assert row["job_title"] == "Cloud Infrastructure Engineer"


def test_doda_detail_local_skip_uses_zero_classifier_calls_and_reports_reason(tmp_path, monkeypatch, capsys):
    ref = _ref(8, "ITエンジニア")
    _setup(tmp_path, monkeypatch, [ref])
    original = FakeDodaAdapter.get_scout_detail

    def actual_role(self, page, item, *, max_age_days=14):
        return [{**original(self, page, item, max_age_days=max_age_days)[0],
                 "job_title": "品質テストスタッフ"}]

    monkeypatch.setattr(FakeDodaAdapter, "get_scout_detail", actual_role)
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("detail local skip must use zero classifier calls")
    ))
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert "DETAIL_LOCAL_SKIP: 1" in capsys.readouterr().out
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        row = db.get_scan_audit(run_id)[0]
        assert row["list_title"] == "ITエンジニア"
        assert row["job_title"] == "品質テストスタッフ"
        assert row["detail_decision"] == "DETAIL_LOCAL_SKIP"
        assert "品質テスト" in row["detail_reason"]
        assert db.conn.execute("SELECT provider FROM evaluations").fetchone()[0] == "local"
    import json
    report = next((tmp_path / "output").glob("*.json"))
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["scan_stats"]["DETAIL_LOCAL_SKIP"] == 1
    assert data["detail_local_skips"] == [{
        "company": ref.company_name,
        "job_title": "品質テストスタッフ",
        "reason": row["detail_reason"],
    }]
    html = next((tmp_path / "output").glob("*.html")).read_text(encoding="utf-8")
    assert "DETAIL_LOCAL_SKIP：1" in html
    assert "品質テストスタッフ" in html
    assert row["detail_reason"] in html


def test_doda_existing_codex_evaluation_is_not_replaced(tmp_path, monkeypatch):
    ref = _ref(7, "Cloud Engineer")
    _setup(tmp_path, monkeypatch, [ref])
    with Database(tmp_path / "data" / "scouts.db") as db:
        db.save_doda_list_item(
            external_id=ref.external_id, company_name=ref.company_name,
            job_title=ref.job_title, age_days=ref.age_days, url=ref.url, status="completed",
        )
        scout_id = db.save_scout(Scout(platform="doda", id=ref.external_id, job_title=ref.job_title))
        run_id = db.start_run("fixture")
        db.save_evaluation(
            scout_id, run_id, Evaluation(verdict="KEEP", confidence=0.7, summary="值得人工查看。"),
            provider="codex", model_name="codex-default",
        )
        db.finish_run(run_id)
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("existing evaluation must not be recalculated")
    ))
    assert cli.main(["scan", "--platform", "doda"]) == 0
    assert FakeDodaAdapter.detail_calls == []
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        row = db.conn.execute("SELECT provider,model_name FROM evaluations").fetchone()
        assert tuple(row) == ("codex", "codex-default")
