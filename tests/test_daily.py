"""Daily orchestration uses only fictional Scouts and a temporary SQLite DB."""

from contextlib import contextmanager
from datetime import datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.daily import DAILY_PLATFORMS, run_daily
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.platforms.doda import DodaOfferRef
from scout_agent.platforms.green import GreenScoutRef
from scout_agent.platforms.mynavi import MynaviScoutRef
from scout_agent.platforms.type_jp import TypeScoutRef
from scout_agent.storage.db import Database


def _settings(tmp_path):
    return Settings(tmp_path, None, None, classifier_provider="mock")


class FakePage:
    def __init__(self):
        self.url = ""

    def goto(self, url, **_kwargs):
        self.url = url

    def get_by_role(self, _role, *, name):
        assert name == "企業からのスカウト受信一覧"
        return SimpleNamespace(wait_for=lambda **_kwargs: None)

    def close(self):
        pass


class FakeManager:
    def __init__(self, *_args, **kwargs):
        assert kwargs["mode"] == "cdp"

    @contextmanager
    def open(self):
        yield SimpleNamespace(contexts=(SimpleNamespace(new_page=FakePage),))


class FakeAdapterBase:
    detail_calls = 0

    def is_logged_in(self, _page):
        return True

    def normalize_scout(self, raw):
        return Scout.model_validate(raw)


class FakeGreen(FakeAdapterBase):
    scout_list_url = "https://example.invalid/green/list"

    def get_scout_list(self, _page):
        return [GreenScoutRef("green-new", "https://example.invalid/green/detail", "架空Green")]

    def get_scout_detail(self, _page, ref):
        type(self).detail_calls += 1
        return {
            "id": ref.external_id, "platform": "green", "company_name": "架空Green",
            "job_title": "Cloud Engineer", "jd_text": "AWS 基盤の構築を主担当。",
            "salary_text": "年収500万円", "location_text": "東京",
            "url": ref.url,
        }


class FakeType(FakeAdapterBase):
    scout_list_url = "https://example.invalid/type/list"

    def get_scout_list(self, _page, *, limit):
        assert limit == 100
        return [TypeScoutRef(
            "type-new", "https://example.invalid/type/detail", "company_offer",
            datetime.now(ZoneInfo("Asia/Tokyo")).date(), "架空type", "Cloud Engineer",
            "架空 Scout", ("Cloud Engineer",),
        )]

    def get_scout_detail(self, _page, ref, *, include_indices):
        type(self).detail_calls += 1
        assert include_indices == {0}
        return [{
            "id": ref.external_id, "platform": "type", "company_name": "架空type",
            "job_title": "Cloud Engineer", "jd_text": "AWS 基盤の構築を主担当。",
            "salary_text": "年収500万円", "location_text": "大阪",
            "url": ref.url, "_list_index": 0,
        }]


class FakeDoda(FakeAdapterBase):
    scout_list_url = "https://example.invalid/doda/list"
    fail_detail = False

    def iter_scout_list(self, _page, *, limit):
        assert limit == 100
        yield DodaOfferRef(
            "offer:10:job:20", "10", "20", "https://example.invalid/doda/detail",
            "架空doda", "ITエンジニア", 0, "company_offer",
        )

    def get_scout_detail(self, _page, ref, *, max_age_days):
        type(self).detail_calls += 1
        if self.fail_detail:
            raise RuntimeError("fictional doda detail failure")
        return [{
            "id": ref.external_id, "platform": "doda", "company_name": "架空doda",
            "job_title": "ITエンジニア",
            "jd_text": "jobContentOutline: Webアプリ開発を主担当。",
            "url": ref.url,
        }]


class FakeMynavi(FakeAdapterBase):
    scout_list_url = "https://example.invalid/mynavi/list"

    def iter_scout_list(self, _page, *, limit):
        assert limit == 100
        yield MynaviScoutRef(
            "delivery:40:job:123456-5-17-1", "123456-5-17-1", "40",
            "https://example.invalid/mynavi/detail", "架空mynavi", "架空 Scout",
            datetime.now(ZoneInfo("Asia/Tokyo")).date(),
        )

    def get_scout_detail(self, _page, ref, *, max_age_days):
        type(self).detail_calls += 1
        return [{
            "id": ref.external_id, "platform": "mynavi", "company_name": "架空mynavi",
            "job_title": "Cloud Infrastructure Engineer",
            "jd_text": "jobContentOutline: AWS 基盤の設計・構築を主担当。",
            "salary_text": "年収550万円", "location_text": "東京 / Remote",
            "url": ref.url,
        }]


def _fake_adapters(monkeypatch):
    adapters = {"green": FakeGreen, "type": FakeType, "doda": FakeDoda,
                "mynavi": FakeMynavi}
    for name, adapter in adapters.items():
        adapter.detail_calls = 0
        monkeypatch.setitem(cli.ADAPTERS, name, adapter)
    FakeDoda.fail_detail = False
    monkeypatch.setattr(cli, "BrowserManager", FakeManager)


def _seed_protected(db):
    run_id = db.start_run("historical")
    for platform in DAILY_PLATFORMS:
        scout_id = db.save_scout(Scout(
            id=f"historical-{platform}", platform=platform,
            company_name=f"架空旧{platform}", job_title="Infrastructure Engineer",
            jd_text="架空の既存 JD。",
        ))
        db.save_evaluation(
            scout_id, run_id, Evaluation(
                verdict="KEEP", confidence=0.8, summary="已有评价，必须保留。",
            ), provider="codex", model_name="protected-test",
        )
    db.finish_run(run_id)


def _protected_rows(db):
    return [tuple(row) for row in db.conn.execute(
        "SELECT s.platform,s.external_id,e.payload,e.provider,e.model_name,e.evaluated_at "
        "FROM scouts s JOIN evaluations e ON e.scout_id=s.id "
        "WHERE s.external_id LIKE 'historical-%' ORDER BY s.platform"
    )]


def test_daily_cli_mock_smoke_dedupes_and_preserves_history(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _fake_adapters(monkeypatch)
    with Database(settings.db_path) as db:
        _seed_protected(db)
        before = _protected_rows(db)
    assert cli.main(["daily"]) == 0
    first_stdout = capsys.readouterr().out
    assert "Daily classifier: evaluated=3 pending=0" in first_stdout
    assert "Daily verdicts: KEEP=3 MAYBE=0 SKIP=1" in first_stdout
    report_path = sorted(settings.output_path.glob("*_daily_report.json"))[-1]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["totals"] == {
        "list_scanned": 4, "new": 4, "already_seen": 0,
        "local_skip": 1, "sent_to_classifier": 3,
        "KEEP": 3, "MAYBE": 0, "SKIP": 1,
    }
    assert len(report["keep"]) == 3 and report["maybe"] == []
    mynavi_card = next(card for card in report["keep"] if card["platform"] == "mynavi")
    assert mynavi_card["salary"] == "年収550万円"
    assert mynavi_card["location"] == "東京 / Remote"
    assert mynavi_card["remote"] == "Remote"
    assert mynavi_card["url"] == "https://example.invalid/mynavi/detail"
    assert mynavi_card["summary"] and mynavi_card["reasons"] and mynavi_card["concerns"]
    assert "jd_text" not in report_path.read_text(encoding="utf-8")
    assert "scout_text" not in report_path.read_text(encoding="utf-8")
    with Database(settings.db_path, read_only=True) as db:
        assert _protected_rows(db) == before
    detail_counts = {name: adapter.detail_calls for name, adapter in (
        ("green", FakeGreen), ("type", FakeType),
        ("doda", FakeDoda), ("mynavi", FakeMynavi),
    )}
    monkeypatch.setattr(cli, "_classifier", lambda _: (_ for _ in ()).throw(
        AssertionError("seen records must consume zero model tokens")
    ))
    assert cli.main(["daily"]) == 0
    second_stdout = capsys.readouterr().out
    assert "Daily classifier: evaluated=0 pending=0" in second_stdout
    assert "Daily verdicts: KEEP=0 MAYBE=0 SKIP=0" in second_stdout
    assert detail_counts == {name: adapter.detail_calls for name, adapter in (
        ("green", FakeGreen), ("type", FakeType),
        ("doda", FakeDoda), ("mynavi", FakeMynavi),
    )}
    with Database(settings.db_path, read_only=True) as db:
        assert _protected_rows(db) == before


def test_daily_platform_failure_continues_and_retries_pending(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _fake_adapters(monkeypatch)
    FakeDoda.fail_detail = True
    assert cli.main(["daily"]) == 1
    output = capsys.readouterr().out
    assert "Daily mynavi:" in output
    assert "Daily errors:" in output and "doda" in output
    with Database(settings.db_path, read_only=True) as db:
        assert db.doda_list_status("offer:10:job:20") == "pending"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.platform='mynavi'"
        ).fetchone()[0] == 1
    FakeDoda.fail_detail = False
    assert cli.main(["daily"]) == 0
    with Database(settings.db_path, read_only=True) as db:
        assert db.doda_list_status("offer:10:job:20") == "completed"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.platform='doda' AND e.provider='local'"
        ).fetchone()[0] == 1


def test_daily_dry_run_never_opens_browser_or_model_or_writes(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "BrowserManager", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("browser must not be accessed")
    ))
    monkeypatch.setattr(cli, "_classifier", lambda _: (_ for _ in ()).throw(
        AssertionError("model must not be accessed")
    ))
    with Database(settings.db_path) as db:
        _seed_protected(db)
        before = _protected_rows(db)
        runs = db.conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
    assert cli.main(["daily", "--dry-run"]) == 0
    assert "green -> type -> doda -> mynavi" in capsys.readouterr().out
    with Database(settings.db_path, read_only=True) as db:
        assert _protected_rows(db) == before
        assert db.conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == runs
    assert not settings.output_path.exists()


def test_daily_codex_batches_across_platforms_without_real_cli(tmp_path):
    settings = Settings(tmp_path, None, None, classifier_provider="codex", codex_batch_size=8)

    def fake_scan(platform, capture):
        capture["stats"] = {"list_scanned": 0, "already_seen": 0}
        if platform in {"green", "type"}:
            with Database(settings.db_path) as db:
                for index in range(5 if platform == "green" else 4):
                    db.save_scout(Scout(
                        id=f"{platform}-{index}", platform=platform, company_name="架空会社",
                        job_title="Cloud Engineer", jd_text="AWS 基盤を構築。",
                    ))
        return 0

    class FakeCodex:
        provider = "codex"
        model_name = "fictional-codex"
        batches = []

        def classify_many(self, scouts):
            self.batches.append(len(scouts))
            return {scout_id: Evaluation(
                verdict="MAYBE", confidence=0.5, summary="需要人工确认。",
            ) for scout_id, _ in reversed(scouts)}

        def classify(self, _scout):
            self.batches.append(1)
            return Evaluation(verdict="MAYBE", confidence=0.5, summary="需要人工确认。")

    fake = FakeCodex()
    assert run_daily(
        settings, dry_run=False, scan_platform=fake_scan,
        classifier_factory=lambda _: fake, finalize_evaluation=lambda value, _: value,
    ) == 0
    assert fake.batches == [8, 1]
    with Database(settings.db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations WHERE provider='codex'").fetchone()[0] == 9
        assert db.conn.execute(
            "SELECT COUNT(*) FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.platform='type' AND e.provider='codex'"
        ).fetchone()[0] == 4


def test_daily_report_counts_new_title_skips_without_skip_cards(tmp_path):
    settings = _settings(tmp_path)

    def fake_scan(platform, capture):
        capture["stats"] = {
            "list_scanned": 2 if platform == "type" else 0,
            "already_seen": 0,
            "new_title_skipped": 2 if platform == "type" else 0,
        }
        return 0

    assert run_daily(
        settings, dry_run=False, scan_platform=fake_scan,
        classifier_factory=lambda _: (_ for _ in ()).throw(AssertionError("no candidate")),
        finalize_evaluation=lambda value, _: value,
    ) == 0
    report_path = next(settings.output_path.glob("*_daily_report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["platforms"]["type"]["new"] == 2
    assert report["platforms"]["type"]["local_skip"] == 2
    assert report["totals"]["SKIP"] == 2
    assert report["keep"] == report["maybe"] == []


def test_daily_failed_later_batch_keeps_saved_results_for_retry(tmp_path):
    settings = Settings(tmp_path, None, None, classifier_provider="codex", codex_batch_size=8)

    def fake_scan(platform, capture):
        capture["stats"] = {"list_scanned": 0, "already_seen": 0}
        if platform == "green":
            with Database(settings.db_path) as db:
                for index in range(9):
                    db.save_scout(Scout(
                        id=f"pending-{index}", platform="green", company_name="架空会社",
                        job_title="Cloud Engineer", jd_text="AWS 基盤を構築。",
                    ))
        return 0

    class InterruptedClassifier:
        provider = "codex"
        model_name = "fictional-codex"

        def classify_many(self, scouts):
            assert len(scouts) == 8
            return {scout_id: Evaluation(
                verdict="MAYBE", confidence=0.5, summary="需要人工确认。",
            ) for scout_id, _ in scouts}

        def classify(self, _scout):
            raise RuntimeError("fictional quota interruption")

    assert run_daily(
        settings, dry_run=False, scan_platform=fake_scan,
        classifier_factory=lambda _: InterruptedClassifier(),
        finalize_evaluation=lambda value, _: value,
    ) == 1
    with Database(settings.db_path, read_only=True) as db:
        first_eight = [tuple(row) for row in db.conn.execute(
            "SELECT scout_id,payload,provider FROM evaluations ORDER BY scout_id"
        )]
        assert len(first_eight) == 8

    class ResumeClassifier(InterruptedClassifier):
        def classify(self, _scout):
            return Evaluation(verdict="MAYBE", confidence=0.5, summary="需要人工确认。")

    assert run_daily(
        settings, dry_run=False, scan_platform=fake_scan,
        classifier_factory=lambda _: ResumeClassifier(),
        finalize_evaluation=lambda value, _: value,
    ) == 0
    with Database(settings.db_path, read_only=True) as db:
        after = [tuple(row) for row in db.conn.execute(
            "SELECT scout_id,payload,provider FROM evaluations ORDER BY scout_id"
        )]
        assert len(after) == 9
        assert after[:8] == first_eight


def test_daily_reports_detail_failure_even_when_platform_scan_returns_zero(tmp_path):
    settings = _settings(tmp_path)
    visited = []

    def fake_scan(platform, capture):
        visited.append(platform)
        capture["stats"] = {
            "list_scanned": 1, "already_seen": 0,
            "detail_failed": 1 if platform == "mynavi" else 0,
        }
        return 0

    assert run_daily(
        settings, dry_run=False, scan_platform=fake_scan,
        classifier_factory=lambda _: (_ for _ in ()).throw(AssertionError("no candidate")),
        finalize_evaluation=lambda value, _: value,
    ) == 1
    assert visited == list(DAILY_PLATFORMS)
    report_path = next(settings.output_path.glob("*_daily_report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "detail fetch(es) failed" in report["errors"]["mynavi"]
    assert report["platforms"]["doda"]["error"] is None
