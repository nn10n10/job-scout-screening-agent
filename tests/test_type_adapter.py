from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.platforms.type_jp import (
    TypeJpAdapter,
    TypeScoutRef,
    parse_type_day,
    parse_type_ref,
    prefilter_jobs,
    prefilter_title,
    title_is_clear_non_target,
    type_detail_local_skip_reason,
    type_post_detail_hard_rule,
    type_ses_hard_rule,
)
from scout_agent.storage.db import Database


def test_type_uses_stable_mid_not_reused_user_id():
    first = parse_type_ref(
        "/scout/offerdm_detail/?mid=10000001&offerdmUserId=900000001",
        "09/26(土)", today=date(2026, 9, 26), company_name="架空社",
    )
    second = parse_type_ref(
        "/scout/offerdm_detail/?mid=10000002&offerdmUserId=900000001",
        "09/26(土)", today=date(2026, 9, 26), company_name="架空社",
    )
    scout = parse_type_ref(
        "/scout/scout_detail/?mid=10000001&scoutUserId=900000002",
        "09/26(土)", today=date(2026, 9, 26), job_titles=["Cloud Engineer"],
    )
    assert {first.external_id, second.external_id, scout.external_id} == {
        "offer_dm:10000001", "offer_dm:10000002", "scout:10000001",
    }
    assert first.received_on == date(2026, 9, 26)
    assert first.company_name == "架空社"
    assert scout.job_title == "Cloud Engineer"
    assert first.job_title is None


@pytest.mark.parametrize("href", [
    "/scout/offerdm_detail/?offerdmUserId=900000001",
    "/scout/offerdm_detail/?mid=abc&offerdmUserId=900000001",
    "/scout/offerdm_detail/?mid=1&scoutUserId=900000001",
    "https://example.test/scout/offerdm_detail/?mid=1&offerdmUserId=2",
    "/entry/confirm_simple/1/?mid=1&offerdmUserId=2",
])
def test_type_rejects_non_message_links(href):
    with pytest.raises(ValueError):
        parse_type_ref(href, "09/26(土)", today=date(2026, 9, 26))


def test_type_day_uses_weekday_and_year_boundary():
    assert parse_type_day("12/31(木)", today=date(2027, 1, 1)) == date(2026, 12, 31)
    assert parse_type_day("09/26(土)", today=date(2026, 9, 26)) == date(2026, 9, 26)
    assert parse_type_day("09/26(金)", today=date(2026, 9, 26)) == date(2025, 9, 26)
    assert parse_type_day("09/26(月)", today=date(2026, 9, 26)) is None
    assert parse_type_day("unknown", today=date(2026, 9, 26)) is None


class FakeLocator:
    def __init__(self, value=None, links=None):
        self.value = value
        self.links = links

    def count(self):
        return len(self.links) if self.links is not None else int(self.value is not None)

    @property
    def first(self):
        return self

    def all(self):
        return self.links or []

    def inner_text(self):
        return self.value

    def all_inner_texts(self):
        return self.value or []


class FakeLink:
    def __init__(self, index):
        self.index = index

    def get_attribute(self, name):
        assert name == "href"
        return f"/scout/offerdm_detail/?mid={10000000 + self.index}&offerdmUserId=900000001"

    def locator(self, selector):
        assert selector == "xpath=ancestor::section[1]"
        return FakeCard()


class FakeCard:
    def count(self):
        return 1

    def locator(self, selector):
        if "date-unit" in selector:
            return FakeLocator("09/26(土)")
        if selector == "p.company-name":
            return FakeLocator("架空社")
        if selector == "p.job-name":
            return FakeLocator(["Cloud Engineer"])
        if selector == "h2":
            return FakeLocator("架空のご案内")
        raise AssertionError(selector)


def test_type_list_reads_at_most_configured_limit_without_pagination(monkeypatch):
    class FakePage:
        def locator(self, selector):
            assert "offerdm_detail" in selector
            return FakeLocator(links=[FakeLink(index) for index in range(140)])

    monkeypatch.setattr("scout_agent.platforms.type_jp.datetime", SimpleNamespace(
        now=lambda _tz: SimpleNamespace(date=lambda: date(2026, 9, 26)),
    ))
    refs = TypeJpAdapter().get_scout_list(FakePage())
    assert len(refs) == 100
    assert refs[0].external_id == "offer_dm:10000000"
    assert refs[-1].external_id == "offer_dm:10000099"


def test_type_title_prefilter_is_conservative():
    for title in ("ITエンジニア", "SE", "インフラエンジニア", "バックエンドエンジニア",
                  "Cloud Engineer", "AWS エンジニア", "法人営業／Cloud Engineer", "未知の職種"):
        assert not title_is_clear_non_target((title,))
    assert title_is_clear_non_target(("法人営業",))
    assert title_is_clear_non_target(("フロントエンドエンジニア",))
    assert title_is_clear_non_target(("プロジェクトマネージャー",))
    assert not title_is_clear_non_target(("法人営業", "インフラエンジニア"))


@pytest.mark.parametrize(("titles", "expected"), [
    (("開発エンジニア", "インフラエンジニア"), ("TITLE_SKIP", "DETAIL")),
    (("SRE", "PM", "ITエンジニア"), ("DETAIL", "TITLE_SKIP", "TITLE_SKIP")),
    (("買取営業", "買取スタッフ"), ("TITLE_SKIP", "TITLE_SKIP")),
    (("ITエンジニア",), ("DETAIL",)),
    (("SE",), ("DETAIL",)),
    (("インフラエンジニア",), ("DETAIL",)),
    (("Backend Engineer",), ("TITLE_REVIEW",)),
    (("社内SE",), ("TITLE_REVIEW",)),
    (("Security Engineer",), ("TITLE_REVIEW",)),
    (("Consulting Engineer",), ("TITLE_REVIEW",)),
])
def test_type_per_job_prefilter_examples(titles, expected):
    assert tuple(item.decision for item in prefilter_jobs(titles)) == expected


@pytest.mark.parametrize("title", [
    "法人営業", "買取PR職", "施工管理", "データ登録事務", "送迎ドライバー",
    "ビル・マンションの管理", "SNS運用スタッフ", "Webデザイナー",
    "動画編集クリエイター", "人材管理スタッフ",
])
def test_type_obvious_non_target_roles_are_skipped(title):
    assert prefilter_title(title).decision == "TITLE_SKIP"


def test_type_ses_signals_do_not_skip_explicit_target_roles():
    for role in (
        "Cloud Engineer", "クラウドエンジニア", "Infrastructure Engineer",
        "インフラエンジニア", "SRE", "DevOps Engineer", "Platform Engineer",
        "AWS Engineer", "Network Engineer", "ネットワークエンジニア",
        "Server Engineer", "サーバーエンジニア",
    ):
        assert prefilter_title(f"{role}｜還元率80％｜案件選択制").decision == "DETAIL"
    combined = prefilter_title("SE｜還元率80％｜案件選択制")
    assert combined.decision == "TITLE_SKIP"
    assert "還元率" in combined.reason and "案件選択" in combined.reason
    assert prefilter_title(
        "自社サービスのインフラエンジニア｜還元率80％｜案件選択制"
    ).decision == "DETAIL"
    assert prefilter_title("開発エンジニア｜還元率80％｜単価連動").decision == "TITLE_SKIP"


def test_type_post_detail_hard_rule_requires_jd_evidence():
    title = "クラウドエンジニア｜還元率80％｜案件選択制"
    assert type_post_detail_hard_rule(Scout(platform="type", job_title=title, jd_text="AWS 基盤構築。")) is None
    result = type_post_detail_hard_rule(Scout(
        platform="type", job_title=title, jd_text="客先常駐でインフラ構築を担当。",
    ))
    assert result is not None and result.verdict == "SKIP" and result.client_site is True
    assert type_post_detail_hard_rule(Scout(
        platform="type", job_title=title, jd_text="客先常駐なし。自社内でクラウド基盤構築。",
    )) is None


@pytest.mark.parametrize("title,jd", [
    ("Webエンジニア", "Webアプリケーションの開発を担当。"),
    ("サーバーサイドエンジニア", "業務系システムの開発案件を担当。AWS を使用。"),
    ("アーキテクトエンジニア", "自動車関連の組み込み開発プロジェクトをメインに担当。"),
    ("ITサポート", "ITサポート事務としてヘルプデスクを担当。"),
    ("初級ITエンジニア", "1ヶ月の研修後にプロジェクトへ配属。案件例：サーバ監視、ヘルプデスク。"),
])
def test_type_detail_local_skip_requires_non_target_primary_duty(title, jd):
    assert type_detail_local_skip_reason(Scout(platform="type", job_title=title, jd_text=jd))


def test_type_detail_local_skip_keeps_mixed_or_infra_primary_duties():
    mixed = Scout(
        platform="type", job_title="ITエンジニア",
        jd_text="Webアプリ開発、ヘルプデスク、ネットワーク/サーバー/クラウドの設計・構築から配属。",
    )
    infra = Scout(
        platform="type", job_title="インフラエンジニア",
        jd_text="ネットワークとサーバの設計・構築が中心。ヘルプデスクにも対応。",
    )
    assert type_detail_local_skip_reason(mixed) is None
    assert type_detail_local_skip_reason(infra) is None


def test_type_ses_hard_rule_requires_site_and_strong_signal_for_infra():
    scout = Scout(
        platform="type", job_title="インフラエンジニア｜還元率83％",
        jd_text="AWS 基盤の設計・構築を担当。案件は100％選択制。",
        location_text="首都圏のプロジェクト先に配属。",
    )
    assert type_ses_hard_rule(scout).verdict == "SKIP"
    assert type_ses_hard_rule(scout.model_copy(update={
        "location_text": "自社内勤務。客先常駐なし。受託開発中心。",
    })) is None
    assert type_ses_hard_rule(scout.model_copy(update={
        "job_title": "インフラエンジニア",
        "jd_text": "AWS 基盤の設計・構築を担当。直請けのチーム案件あり。",
    })) is None


def test_type_ses_hard_rule_skips_ambiguous_project_assignment():
    scout = Scout(
        platform="type", job_title="ITエンジニア",
        jd_text="Web開発やクラウド構築など複数の職種から、研修後にプロジェクトへ配属。",
    )
    assert type_detail_local_skip_reason(scout) is None
    assert type_ses_hard_rule(scout).verdict == "SKIP"
    inhouse = Scout(
        platform="type", job_title="ITエンジニア｜還元率80％",
        jd_text="社内業務の開発とインフラ構築を担当。",
        location_text="自社内勤務。客先常駐なし。受託中心。",
    )
    assert type_ses_hard_rule(inhouse) is None


def test_type_ses_hard_rule_accepts_pool_plus_two_commercial_signals_without_site():
    scout = Scout(
        platform="type", job_title="インフラエンジニア",
        jd_text="AWS 基盤の設計・構築を担当。常時1,200件の案件から希望に合う案件をご紹介。",
        salary_text="単価連動型、還元率82％。待機時給与保証あり。",
        location_text="全国からリモート勤務可。",
    )
    result = type_ses_hard_rule(scout)
    assert result is not None and result.verdict == "SKIP"
    assert "案件池介绍配属" in result.reasons[0]
    assert "単価連動" in result.reasons[0] and "還元率" in result.reasons[0]
    assert result.client_site is None
    assert type_ses_hard_rule(scout.model_copy(update={
        "salary_text": "還元率82％。",
    })) is None
    assert type_ses_hard_rule(scout.model_copy(update={
        "jd_text": "AWS 基盤の設計・構築を担当。受託・直請けのチーム案件あり。",
    })) is None
    assert type_ses_hard_rule(scout.model_copy(update={
        "location_text": "自社内勤務。客先常駐なし。",
    })) is None


def _ref(index: int, received_on: date) -> TypeScoutRef:
    return TypeScoutRef(
        external_id=f"offer_dm:{10000000 + index}",
        url=f"https://type.jp/scout/offerdm_detail/?mid={10000000 + index}&offerdmUserId=900000001",
        scout_kind="offer_dm", received_on=received_on,
        company_name="架空社", job_title="Cloud Engineer", scout_title="架空のご案内",
        job_titles=("Cloud Engineer",),
    )


class FakePage:
    def __init__(self):
        self.url = ""
        self.closed = False

    def goto(self, url, **_kwargs):
        self.url = url

    def locator(self, _selector):
        return SimpleNamespace(count=lambda: 36)

    def close(self):
        self.closed = True


class FakeTypeAdapter:
    scout_list_url = "https://type.jp/scout/"
    refs: list[TypeScoutRef] = []
    detail_calls: list[str] = []

    def is_logged_in(self, page):
        return page.url == self.scout_list_url

    def get_scout_list(self, _page, *, limit=100):
        return self.refs[:limit]

    def get_scout_detail(self, _page, ref, *, include_indices=None):
        self.detail_calls.append(ref.external_id)
        assert include_indices == {0}
        return [{
            "id": ref.external_id, "platform": "type", "company_name": ref.company_name,
            "job_title": ref.job_title, "scout_title": ref.scout_title,
            "scout_text": "架空の Cloud Engineer 募集。", "jd_text": "AWS Terraform 基盤構築。",
            "received_on": ref.received_on, "scout_kind": ref.scout_kind,
            "sender_kind": None, "is_bulk_like": None, "url": ref.url,
            "_list_index": 0,
        }]

    def normalize_scout(self, raw):
        return Scout.model_validate(raw)


def _mock_type_scan(tmp_path, monkeypatch, refs):
    FakeTypeAdapter.refs = refs
    FakeTypeAdapter.detail_calls = []
    monkeypatch.setitem(cli.ADAPTERS, "type", FakeTypeAdapter)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(tmp_path, None, None, classifier_provider="mock"))
    page = FakePage()

    class FakeManager:
        def __init__(self, *_args, **kwargs):
            assert kwargs["mode"] == "cdp"

        @contextmanager
        def open(self):
            yield SimpleNamespace(contexts=(SimpleNamespace(new_page=lambda: page),))

    monkeypatch.setattr(cli, "BrowserManager", FakeManager)
    return page


def test_type_scan_mock_dedupes_second_run_without_classifier_or_detail(tmp_path, monkeypatch, capsys):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    page = _mock_type_scan(tmp_path, monkeypatch, [_ref(index, today) for index in range(15)])
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert len(FakeTypeAdapter.detail_calls) == 15
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.count_processed() == 15
        assert db.conn.execute("SELECT DISTINCT provider FROM evaluations").fetchone()[0] == "mock"
    monkeypatch.setattr(cli, "_classifier", lambda _settings: SimpleNamespace(
        provider="mock", model_name="mock", classify=lambda _scout: (_ for _ in ()).throw(
            AssertionError("existing Scout must use zero classifier tokens")
        ),
    ))
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert len(FakeTypeAdapter.detail_calls) == 15
    assert "New scouts: 0" in capsys.readouterr().out
    assert page.closed


@pytest.mark.parametrize("title,jd,location,model_name", [
    ("Webエンジニア", "Webアプリケーションの開発を担当。", "自社内勤務。",
     "type-detail-prefilter"),
    ("インフラエンジニア｜還元率83％",
     "AWS 基盤の設計・構築を担当。案件は100％選択制。",
     "首都圏のプロジェクト先に配属。", "type-ses-hard-rule"),
])
def test_type_scan_local_rules_do_not_construct_classifier(
    tmp_path, monkeypatch, title, jd, location, model_name,
):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    ref = TypeScoutRef(**{
        **_ref(21, today).__dict__, "job_title": title, "job_titles": (title,),
    })
    _mock_type_scan(tmp_path, monkeypatch, [ref])

    class LocalOnlyAdapter(FakeTypeAdapter):
        def get_scout_detail(self, _page, selected, *, include_indices=None):
            return [{
                "id": selected.external_id, "platform": "type",
                "company_name": "架空基盤社", "job_title": title,
                "jd_text": jd, "location_text": location,
                "received_on": selected.received_on, "url": selected.url,
                "_list_index": 0,
            }]

    monkeypatch.setitem(cli.ADAPTERS, "type", LocalOnlyAdapter)
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("local skip must not construct a classifier")
    ))
    assert cli.main(["scan", "--platform", "type"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        row = db.conn.execute("SELECT provider,model_name,payload FROM evaluations").fetchone()
        assert row["provider"] == "local"
        assert row["model_name"] == model_name
        assert Evaluation.model_validate_json(row["payload"]).verdict == "SKIP"


def test_type_skips_old_items_without_opening_their_details(tmp_path, monkeypatch, capsys):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    _mock_type_scan(tmp_path, monkeypatch, [
        _ref(1, today), _ref(2, today - timedelta(days=31)), _ref(3, today),
    ])
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == ["offer_dm:10000001", "offer_dm:10000003"]
    output = capsys.readouterr().out
    assert "too_old: 1" in output
    assert "New scouts: 2" in output


def test_type_title_skip_is_persisted_and_never_classified(tmp_path, monkeypatch, capsys):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    irrelevant = TypeScoutRef(**{
        **_ref(1, today).__dict__, "job_title": "法人営業", "job_titles": ("法人営業",),
    })
    _mock_type_scan(tmp_path, monkeypatch, [irrelevant, _ref(2, today)])
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == ["offer_dm:10000002"]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.type_list_status(irrelevant.external_id) == "title_skip"
        assert db.count_processed() == 1
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("second scan must not construct classifier")
    ))
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == ["offer_dm:10000002"]
    assert "already_seen: 2" in capsys.readouterr().out


def test_type_all_skipped_offer_audits_each_job_without_detail(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    ref = TypeScoutRef(**{
        **_ref(7, today).__dict__, "job_title": None,
        "job_titles": ("買取営業", "データ登録事務"),
    })
    _mock_type_scan(tmp_path, monkeypatch, [ref])
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("all-skipped offer must not construct classifier")
    ))
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == []
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        assert db.type_list_status(ref.external_id) == "title_skip"
        assert [(row["job_title"], row["decision"], row["detail_fetched"])
                for row in db.get_scan_audit(run_id)] == [
                    ("買取営業", "TITLE_SKIP", 0), ("データ登録事務", "TITLE_SKIP", 0),
                ]
    assert cli.main(["scan", "--platform", "type"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        assert all(row["already_seen"] == 1 for row in db.get_scan_audit(run_id))


def test_type_stops_only_after_thirty_consecutive_seen(tmp_path, monkeypatch, capsys):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    refs = [_ref(index, today) for index in range(31)]
    _mock_type_scan(tmp_path, monkeypatch, refs)
    with Database(tmp_path / "data" / "scouts.db") as db:
        for ref in refs[:30]:
            db.save_type_list_item(
                external_id=ref.external_id, company_name=ref.company_name,
                job_title=ref.job_title, received_on=today.isoformat(),
                received_at=None, url=ref.url, status="completed",
            )
    monkeypatch.setattr(cli, "_classifier", lambda _settings: (_ for _ in ()).throw(
        AssertionError("seen-only scan must not construct classifier")
    ))
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == []
    output = capsys.readouterr().out
    assert "list_scanned: 30" in output
    assert "already_seen: 30" in output


def test_type_pending_item_is_resumed_past_seen_threshold(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    refs = [_ref(index, today) for index in range(31)]
    _mock_type_scan(tmp_path, monkeypatch, refs)
    with Database(tmp_path / "data" / "scouts.db") as db:
        for ref in refs:
            db.save_type_list_item(
                external_id=ref.external_id, company_name=ref.company_name,
                job_title=ref.job_title, received_on=today.isoformat(),
                received_at=None, url=ref.url,
                status="pending" if ref == refs[-1] else "completed",
            )
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == [refs[-1].external_id]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        assert db.type_list_status(refs[-1].external_id) == "completed"


def test_one_seen_item_does_not_stop_new_scout_or_replace_codex(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    seen, fresh = _ref(1, today), _ref(2, today)
    _mock_type_scan(tmp_path, monkeypatch, [seen, fresh])
    with Database(tmp_path / "data" / "scouts.db") as db:
        run_id = db.start_run("type")
        scout_id = db.save_scout(Scout(id=seen.external_id, platform="type", url=seen.url))
        db.save_evaluation(
            scout_id, run_id, Evaluation(verdict="MAYBE", confidence=0.5, summary="保留既有评价。"),
            provider="codex", model_name="codex-default",
        )
        db.finish_run(run_id)
    assert cli.main(["scan", "--platform", "type"]) == 0
    assert FakeTypeAdapter.detail_calls == [fresh.external_id]
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        row = db.conn.execute(
            "SELECT e.provider,e.model_name,e.payload FROM evaluations e "
            "JOIN scouts s ON s.id=e.scout_id WHERE s.platform='type' AND s.external_id=?",
            (seen.external_id,),
        ).fetchone()
        assert row["provider"] == "codex"
        assert row["model_name"] == "codex-default"
        assert "保留既有评价" in row["payload"]


def test_type_multi_job_pipeline_classifies_only_target_job(tmp_path, monkeypatch, capsys):
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    ref = TypeScoutRef(**{
        **_ref(5, today).__dict__, "job_title": None,
        "job_titles": ("Cloud Engineer", "法人営業"),
    })
    _mock_type_scan(tmp_path, monkeypatch, [ref])

    class MultiJobAdapter(FakeTypeAdapter):
        def get_scout_detail(self, _page, selected, *, include_indices=None):
            self.detail_calls.append(selected.external_id)
            assert include_indices == {0}
            return [
                {"id": f"{selected.external_id}:job:456", "platform": "type",
                 "job_title": "Cloud Engineer", "jd_text": "AWS Terraform 基盤構築。",
                 "url": selected.url, "_list_index": 0},
            ]

    monkeypatch.setitem(cli.ADAPTERS, "type", MultiJobAdapter)
    assert cli.main(["scan", "--platform", "type"]) == 0
    with Database(tmp_path / "data" / "scouts.db", read_only=True) as db:
        rows = db.conn.execute(
            "SELECT s.external_id,e.provider FROM scouts s "
            "JOIN evaluations e ON e.scout_id=s.id ORDER BY s.external_id"
        ).fetchall()
        assert [(row["external_id"], row["provider"]) for row in rows] == [
            (f"{ref.external_id}:job:456", "mock"),
        ]
        assert db.conn.execute(
            "SELECT job_title FROM type_list_items WHERE external_id=?", (ref.external_id,)
        ).fetchone()[0] == "Cloud Engineer / 法人営業"
        run_id = db.conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
        audit = db.get_scan_audit(run_id)
        assert [(row["external_id"], row["decision"], row["detail_fetched"]) for row in audit] == [
            (f"{ref.external_id}:job:456", "DETAIL", 1),
            (f"{ref.external_id}:list:2", "TITLE_SKIP", 0),
        ]
        assert all(row["scan_run_id"] == run_id and row["platform"] == "type" for row in audit)
        assert all(row["reason"] for row in audit)
    output = capsys.readouterr().out
    assert "TITLE_SKIP: 1" in output
    assert "locally_skipped: 0" in output
    assert "sent_to_classifier: 1" in output


def test_type_multi_job_offer_is_split_without_mixing_jds(monkeypatch):
    ref = TypeScoutRef(**{
        **_ref(10, date(2026, 9, 26)).__dict__, "job_title": None,
        "job_titles": ("Cloud Engineer", "法人営業"),
    })

    class Locator:
        def __init__(self, value=None):
            self.value = value

        @property
        def first(self):
            return self

        def count(self):
            return 1 if self.value is not None else 0

        def inner_text(self):
            return self.value

        def all_inner_texts(self):
            return ["Cloud Engineer", "法人営業"]

        def evaluate_all(self, _script):
            return [
                {"href": "/job-123/456_detail/", "title": "Cloud Engineer"},
                {"href": "/job-123/789_detail/", "title": "法人営業"},
            ]

        def locator(self, selector):
            if selector == "p.company-name":
                return Locator("架空社")
            if selector == "p.main-text":
                return Locator("架空の案内")
            return Locator("present")

    class Detail:
        def __init__(self):
            self.url = ""

        def goto(self, url, **_kwargs):
            self.url = url

        def locator(self, _selector):
            return Locator("present")

        def close(self):
            pass

    reads = []

    def fake_read_job(_page, url):
        reads.append(url)
        return {
            "title": "Cloud Engineer" if "456_" in url else "法人営業",
            "jd_text": "AWS 基盤" if "456_" in url else "営業活動",
            "salary_text": None, "location_text": None,
        }

    monkeypatch.setattr(TypeJpAdapter, "_read_job", staticmethod(fake_read_job))
    page = SimpleNamespace(context=SimpleNamespace(new_page=Detail))
    records = TypeJpAdapter().get_scout_detail(page, ref)
    assert [item["id"] for item in records] == [
        "offer_dm:10000010:job:456", "offer_dm:10000010:job:789",
    ]
    assert records[0]["jd_text"] == "AWS 基盤"
    assert records[1]["jd_text"] == "営業活動"
    reads.clear()
    filtered = TypeJpAdapter().get_scout_detail(page, ref, include_indices={0})
    assert [item["id"] for item in filtered] == ["offer_dm:10000010:job:456"]
    assert len(reads) == 1 and "456_detail" in reads[0]
