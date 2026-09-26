import json
import subprocess

import pytest

from scout_agent import cli
from scout_agent.config import Settings, load_settings
from scout_agent.llm import codex as codex_module
from scout_agent.llm.codex import CodexClassifier
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database


def _evaluation(summary="职位值得进一步查看。"):
    return Evaluation(verdict="KEEP", confidence=0.7, summary=summary)


def _seed(tmp_path, *, mock_count=8, codex_count=22):
    db_path = tmp_path / "data" / "scouts.db"
    with Database(db_path) as db:
        run_id = db.start_run("green")
        for provider, count in (("codex", codex_count), ("mock", mock_count)):
            for index in range(count):
                scout = Scout(
                    id=f"{provider}-{index}", platform="green", company_name="架空科技",
                    job_title="Cloud Engineer", jd_text="AWS Terraform",
                )
                scout_id = db.save_scout(scout)
                db.save_evaluation(
                    scout_id, run_id, _evaluation(f"{provider} 原评价。"),
                    provider=provider, model_name="codex-default" if provider == "codex" else "mock",
                )
        generic_id = db.save_scout(Scout(id="old-generic", platform="generic", jd_text="AWS"))
        db.save_evaluation(
            generic_id, run_id, _evaluation("旧评价。"), provider="legacy", model_name="unknown",
        )
        db.finish_run(run_id)
    return db_path


def _settings(tmp_path, *, batch_size=8):
    return Settings(
        tmp_path, None, None, classifier_provider="codex",
        codex_reasoning_effort="low", codex_batch_size=batch_size,
    )


def _batch_ids(prompt):
    records = json.loads(prompt.split("Scout/JD records:\n", 1)[1])
    return [record["scout_id"] for record in records]


def _batch_output(ids):
    return json.dumps([
        {"scout_id": scout_id, "evaluation": _evaluation(f"编号 {scout_id} 值得查看。").model_dump()}
        for scout_id in reversed(ids)
    ], ensure_ascii=False)


def test_replace_provider_selects_only_eight_mock_and_never_codex(tmp_path):
    db_path = _seed(tmp_path)
    with Database(db_path, read_only=True) as db:
        selected = db.get_scouts_for_evaluation(platform="green", replace_provider="mock")
        assert len(selected) == 8
        assert {scout.id for _, scout in selected} == {f"mock-{i}" for i in range(8)}
        assert db.conn.execute(
            "SELECT COUNT(*) FROM evaluations WHERE provider='codex'"
        ).fetchone()[0] == 22


def test_dry_run_is_read_only_and_does_not_construct_classifier(tmp_path, monkeypatch, capsys):
    db_path = _seed(tmp_path)
    before = db_path.read_bytes()
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "_classifier", lambda _: (_ for _ in ()).throw(AssertionError("classifier called")))
    monkeypatch.setattr(cli, "BrowserManager", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("browser constructed")
    ))
    assert cli.main(["evaluate", "--platform", "green", "--replace-provider", "mock", "--dry-run"]) == 0
    assert capsys.readouterr().out == "Selected Scouts: 8\n"
    assert db_path.read_bytes() == before


def test_codex_classify_many_one_exec_and_reordered_ids(monkeypatch):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert "primary job responsibility" in kwargs["input"]
        assert "AWS/Terraform/CI/CD keywords alone are insufficient" in kwargs["input"]
        assert "--output-schema" not in command  # Direct array; Structured Outputs requires object root.
        assert 'model_reasoning_effort="low"' in command
        assert command[command.index("--model") + 1] == "test-model"
        ids = _batch_ids(kwargs["input"])
        return subprocess.CompletedProcess(command, 0, _batch_output(ids), "")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    classifier = CodexClassifier("test-model", "用简体中文评价", "low")
    scouts = [(str(i), Scout(id=f"external-{i}", platform="green", jd_text="AWS")) for i in range(8)]
    results = classifier.classify_many(scouts)
    assert len(commands) == 1
    assert set(results) == {str(i) for i in range(8)}
    assert all(results[str(i)].summary == f"编号 {i} 值得查看。" for i in range(8))


def test_missing_batch_id_preserves_old_mock_evaluation(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, mock_count=2, codex_count=1)
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path, batch_size=2))
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")

    def fake_run(command, **kwargs):
        ids = _batch_ids(kwargs["input"])
        return subprocess.CompletedProcess(command, 0, _batch_output(ids[:1]), "")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    assert cli.main(["evaluate", "--platform", "green", "--replace-provider", "mock"]) == 1
    output = capsys.readouterr().out
    assert "Evaluated successfully: 1" in output
    assert "Remaining: 1" in output
    with Database(tmp_path / "data" / "scouts.db") as db:
        rows = db.conn.execute(
            "SELECT s.external_id, e.provider, e.payload FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.platform='green' ORDER BY s.id"
        ).fetchall()
        assert rows[0]["provider"] == "codex"
        assert {row["provider"] for row in rows[1:]} == {"mock", "codex"}
        remaining = [row for row in rows if row["provider"] == "mock"]
        assert len(remaining) == 1
        assert json.loads(remaining[0]["payload"])["summary"] == "mock 原评价。"


def test_completed_batch_persists_when_next_batch_hits_quota(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, mock_count=4, codex_count=2)
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path, batch_size=2))
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            return subprocess.CompletedProcess(
                command, 1, "", "5-hour usage limit reached; secret-token=do-not-print",
            )
        return subprocess.CompletedProcess(command, 0, _batch_output(_batch_ids(kwargs["input"])), "")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    assert cli.main(["evaluate", "--platform", "green", "--replace-provider", "mock"]) == 1
    output = capsys.readouterr().out
    assert len(calls) == 2
    assert "Evaluated successfully: 2" in output
    assert "Remaining: 2" in output
    assert "Stopped reason: quota:" in output
    assert "secret-token" not in output
    with Database(tmp_path / "data" / "scouts.db") as db:
        counts = dict(db.conn.execute(
            "SELECT provider, COUNT(*) FROM evaluations GROUP BY provider"
        ).fetchall())
        assert counts == {"codex": 4, "mock": 2, "legacy": 1}
        assert db.conn.execute(
            "SELECT status FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "failed"


def test_conditional_replace_never_overwrites_codex(tmp_path):
    db_path = _seed(tmp_path, mock_count=1, codex_count=1)
    with Database(db_path) as db:
        row = db.conn.execute(
            "SELECT e.scout_id, e.payload FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE s.external_id='codex-0'"
        ).fetchone()
        run_id = db.start_run("evaluate:green")
        assert not db.replace_evaluation_if_provider(
            row["scout_id"], run_id, _evaluation("不应覆盖。"),
            expected_provider="mock", provider="codex", model_name="test-model",
        )
        unchanged = db.conn.execute(
            "SELECT payload, provider FROM evaluations WHERE scout_id=?", (row["scout_id"],)
        ).fetchone()
        assert unchanged["provider"] == "codex"
        assert unchanged["payload"] == row["payload"]


def test_batch_size_config_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_BATCH_SIZE", "8")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "low")
    settings = load_settings(tmp_path)
    assert settings.codex_batch_size == 8
    assert settings.codex_reasoning_effort == "low"
    monkeypatch.setenv("CODEX_BATCH_SIZE", "0")
    with pytest.raises(ValueError, match="positive integer"):
        load_settings(tmp_path)
