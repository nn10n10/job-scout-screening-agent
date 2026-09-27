"""Fictional stored details only: never use real Scout messages or model quota."""

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database


class CountingClassifier:
    provider = "codex"
    model_name = "fictional-test-model"

    def __init__(self):
        self.calls = []

    def classify(self, scout):
        self.calls.append(scout.id)
        return Evaluation(verdict="MAYBE", confidence=0.5, summary="需要人工确认岗位职责。")


def _settings(tmp_path):
    return Settings(root=tmp_path, gemini_api_key=None, gemini_model=None)


def _seed(db):
    run_id = db.start_run("doda")
    examples = [
        ("app", "doda", "架空开发会社", "アプリケーションエンジニア",
         "jobContentOutline: Webアプリを開発します。", "mock"),
        ("cloud", "doda", "架空基盤会社", "インフラエンジニア",
         "jobContentOutline: AWS 基盤の設計・構築を担当します。", "mock"),
        ("generic", "doda", "架空情報会社", "ITエンジニア",
         "jobContentOutline: 顧客の課題に対応します。", "mock"),
        ("sales", "doda", "架空営業会社", "営業",
         "jobContentOutline: 法人営業を担当します。", "codex"),
        ("no-jd", "doda", "架空未取得会社", "ITエンジニア", None, "mock"),
        ("green", "green", "架空Green会社", "Cloud Engineer", "AWS 基盤の運用。", "mock"),
        ("type", "type", "架空type会社", "SE", "顧客課題への対応。", "mock"),
    ]
    for external_id, platform, company, title, jd, provider in examples:
        scout = Scout(id=external_id, platform=platform, company_name=company,
                      job_title=title, jd_text=jd)
        scout_id = db.save_scout(scout)
        db.save_evaluation(
            scout_id, run_id,
            Evaluation(verdict="MAYBE", confidence=0.4, summary="旧评价。"),
            provider=provider, model_name=f"old-{provider}",
        )
    db.finish_run(run_id)


def test_reprocess_dry_run_is_read_only_and_uses_no_browser_or_model(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "BrowserManager", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("browser must not be opened")))
    monkeypatch.setattr(cli, "_classifier", lambda *a: (_ for _ in ()).throw(
        AssertionError("classifier must not be called")))
    with Database(settings.db_path) as db:
        _seed(db)
        before = [tuple(row) for row in db.conn.execute(
            "SELECT s.payload,e.payload,e.provider,e.model_name FROM scouts s "
            "JOIN evaluations e ON e.scout_id=s.id ORDER BY s.id"
        )]
        run_count = db.conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
    assert cli.main(["reprocess", "--platform", "doda", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Local skip: 2" in output
    assert "Classifier candidates: 2" in output
    assert "架空基盤会社 | インフラエンジニア" in output
    assert "架空情報会社 | ITエンジニア" in output
    assert "架空開発会社" not in output
    with Database(settings.db_path, read_only=True) as db:
        after = [tuple(row) for row in db.conn.execute(
            "SELECT s.payload,e.payload,e.provider,e.model_name FROM scouts s "
            "JOIN evaluations e ON e.scout_id=s.id ORDER BY s.id"
        )]
        assert after == before
        assert db.conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == run_count
        assert db.conn.execute("SELECT COUNT(*) FROM local_reprocess").fetchone()[0] == 0


def test_reprocess_preserves_scouts_candidates_and_paid_evaluations(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    with Database(settings.db_path) as db:
        _seed(db)
        before = {row["external_id"]: row["payload"] for row in db.conn.execute(
            "SELECT external_id,payload FROM scouts"
        )}
    assert cli.main(["reprocess", "--platform", "doda"]) == 0
    with Database(settings.db_path) as db:
        after = {row["external_id"]: row["payload"] for row in db.conn.execute(
            "SELECT external_id,payload FROM scouts"
        )}
        assert after == before
        rows = {row["external_id"]: row for row in db.conn.execute(
            "SELECT s.external_id,e.provider,e.model_name,e.payload,lr.decision "
            "FROM scouts s JOIN evaluations e ON e.scout_id=s.id "
            "LEFT JOIN local_reprocess lr ON lr.scout_id=s.id"
        )}
        assert rows["app"]["provider"] == "local"
        assert rows["app"]["model_name"] == "doda-detail-prefilter"
        assert rows["app"]["decision"] == "local_skip"
        assert rows["sales"]["provider"] == "codex"
        assert rows["sales"]["model_name"] == "old-codex"
        assert rows["sales"]["decision"] == "local_skip"
        assert rows["cloud"]["provider"] == "mock"
        assert rows["generic"]["provider"] == "mock"
        assert rows["cloud"]["decision"] == "classifier_candidate"
        assert rows["generic"]["decision"] == "classifier_candidate"
        assert rows["no-jd"]["decision"] is None
        assert rows["green"]["provider"] == "mock"
        assert rows["type"]["provider"] == "mock"
        selected = db.get_scouts_for_evaluation(
            platform="doda", replace_provider="mock", eligible_only=True,
        )
        assert {scout.id for _, scout in selected} == {"cloud", "generic"}


def test_evaluate_eligible_only_limits_mock_replacement(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "BrowserManager", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("browser must not be opened")))
    with Database(settings.db_path) as db:
        _seed(db)
    assert cli.main(["reprocess", "--platform", "doda"]) == 0
    assert cli.main(["evaluate", "--platform", "doda", "--eligible-only",
                     "--replace-provider", "mock", "--dry-run"]) == 0
    assert "Selected Scouts: 2" in capsys.readouterr().out
    classifier = CountingClassifier()
    monkeypatch.setattr(cli, "_classifier", lambda _: classifier)
    assert cli.main(["evaluate", "--platform", "doda", "--eligible-only",
                     "--replace-provider", "mock"]) == 0
    assert set(classifier.calls) == {"cloud", "generic"}
    with Database(settings.db_path) as db:
        rows = {row["external_id"]: row["provider"] for row in db.conn.execute(
            "SELECT s.external_id,e.provider FROM scouts s "
            "JOIN evaluations e ON e.scout_id=s.id"
        )}
        assert rows["cloud"] == rows["generic"] == "codex"
        assert rows["app"] == "local"
        assert rows["no-jd"] == "mock"
        assert rows["green"] == rows["type"] == "mock"
        assert rows["sales"] == "codex"
