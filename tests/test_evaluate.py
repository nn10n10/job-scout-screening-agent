import json
from datetime import datetime

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database


class FakeGemini:
    provider = "gemini"
    model_name = "gemini-test-model"

    def __init__(self):
        self.calls = 0

    def classify(self, scout):
        self.calls += 1
        return Evaluation(verdict="KEEP", confidence=0.8, summary="JD 中的 AWS 与目标匹配。")


def _settings(tmp_path):
    return Settings(root=tmp_path, gemini_api_key=None, gemini_model=None)


def _seed(db, *, evaluated=True, scout_text="Hello"):
    scout = Scout(
        id="fictional-thread", platform="green", company_name="Example Inc.",
        job_title="Cloud Engineer", scout_text=scout_text,
        jd_text="AWS Terraform platform engineer", url="https://example.test/scout/1",
    )
    run_id = db.start_run("green")
    scout_id = db.save_scout(scout)
    if evaluated:
        db.save_evaluation(
            scout_id, run_id,
            Evaluation(verdict="MAYBE", confidence=0.4, summary="old mock"),
        )
    db.finish_run(run_id)
    return scout_id


def test_evaluate_never_opens_browser_and_skips_existing(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def forbidden_browser(*args, **kwargs):
        raise AssertionError("evaluate must not construct a browser manager")

    monkeypatch.setattr(cli, "BrowserManager", forbidden_browser)
    with Database(settings.db_path) as db:
        _seed(db)
        before = db.conn.execute("SELECT payload FROM scouts").fetchone()[0]
    assert cli.main(["evaluate", "--platform", "green"]) == 0
    assert "Classifier provider: mock" in capsys.readouterr().out
    with Database(settings.db_path) as db:
        assert db.conn.execute("SELECT payload FROM scouts").fetchone()[0] == before
        assert db.count_processed() == 1
        row = db.conn.execute("SELECT payload, provider, model_name FROM evaluations").fetchone()
        assert json.loads(row["payload"])["summary"] == "old mock"
        assert row["provider"] == "mock"
        assert row["model_name"] == "mock"


def test_force_replaces_mock_and_persists_gemini_metadata(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    classifier = FakeGemini()
    monkeypatch.setattr(cli, "_classifier", lambda _: classifier)
    monkeypatch.setattr(cli, "BrowserManager", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("browser constructed")
    ))
    with Database(settings.db_path) as db:
        _seed(db)
        before = db.conn.execute("SELECT payload FROM scouts").fetchone()[0]
    assert cli.main(["evaluate", "--platform", "green", "--limit", "1", "--force"]) == 0
    assert classifier.calls == 1
    with Database(settings.db_path) as db:
        assert db.count_processed() == 1
        assert db.conn.execute("SELECT payload FROM scouts").fetchone()[0] == before
        row = db.conn.execute(
            "SELECT payload, provider, model_name, evaluated_at, created_at FROM evaluations"
        ).fetchone()
        assert json.loads(row["payload"])["verdict"] == "KEEP"
        assert row["provider"] == "gemini"
        assert row["model_name"] == "gemini-test-model"
        assert datetime.fromisoformat(row["evaluated_at"])
        assert row["created_at"] != row["evaluated_at"] or row["created_at"] is not None
    first_html = next(settings.output_path.glob("*.html")).read_text(encoding="utf-8")
    assert "Evaluation model: gemini-test-model" in first_html
    assert "Classifier/model: gemini / gemini-test-model" in first_html
    assert cli.main(["evaluate", "--platform", "green"]) == 0
    assert classifier.calls == 1
    second_html = sorted(settings.output_path.glob("*.html"))[-1].read_text(encoding="utf-8")
    assert "Evaluation model: gemini-test-model" in second_html


def test_missing_scout_message_with_jd_is_evaluated(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    with Database(settings.db_path) as db:
        _seed(db, evaluated=False, scout_text=None)
    assert cli.main(["evaluate", "--platform", "green"]) == 0
    with Database(settings.db_path) as db:
        row = db.conn.execute("SELECT payload, provider, model_name, evaluated_at FROM evaluations").fetchone()
        evaluation = Evaluation.model_validate_json(row["payload"])
        assert evaluation.verdict != "SKIP"
        assert cli.MISSING_MESSAGE_CONCERN in evaluation.concerns
        assert row["provider"] == "mock"
        assert row["model_name"] == "mock"
        assert datetime.fromisoformat(row["evaluated_at"])
    report = json.loads(next(settings.output_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["evaluation_model"] == "mock"
    assert report["results"][0]["evaluation"]["model_name"] == "mock"
