import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from scout_agent import cli
from scout_agent.config import Settings, load_settings
from scout_agent.llm import codex as codex_module
from scout_agent.llm.codex import CodexClassifier, CodexClassifierError
from scout_agent.llm.mock import MockClassifier
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database


def _evaluation_json():
    return Evaluation(
        verdict="KEEP", confidence=0.8,
        summary="AWS 职位与目标匹配。",
        reasons=["JD 明确提及 Terraform。"],
        concerns=["株式会社テスト的远程政策尚不明确。"],
    ).model_dump_json()


def test_provider_selection_is_explicit(tmp_path, monkeypatch):
    settings = Settings(tmp_path, None, None, classifier_provider="codex")
    classifier = cli._classifier(settings)
    assert isinstance(classifier, CodexClassifier)
    assert classifier.model_name == "codex-default"
    assert "岗位的主要工作职责" in classifier.rules
    assert "仅出现 AWS、Terraform、CI/CD" in classifier.rules
    assert isinstance(cli._classifier(Settings(tmp_path, "unused-key", None, classifier_provider="mock")), MockClassifier)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        cli._classifier(Settings(tmp_path, None, "gemini-test", classifier_provider="gemini"))
    gemini_arguments = []

    def fake_gemini(api_key, model, rules):
        gemini_arguments.append((api_key, model, rules))
        return object()

    monkeypatch.setattr(cli, "GeminiClassifier", fake_gemini)
    cli._classifier(Settings(tmp_path, "placeholder-key", "gemini-test", classifier_provider="gemini"))
    assert gemini_arguments[0][:2] == ("placeholder-key", "gemini-test")
    assert "岗位的主要工作职责" in gemini_arguments[0][2]
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "codex")
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    assert load_settings(tmp_path).classifier_provider == "codex"


def test_codex_json_maps_to_evaluation_without_project_access(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:2] == ["codex", "exec"]
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        assert "--skip-git-repo-check" in command
        assert "--full-auto" not in command
        assert 'approval_policy="never"' in command
        assert 'web_search="disabled"' in command
        assert "features.shell_tool=false" in command
        assert "--model" not in command
        assert kwargs["cwd"] != str(tmp_path)
        assert "do not run commands" in kwargs["input"].lower()
        assert "Simplified Chinese" in kwargs["input"]
        assert "职位信息不足，需人工确认" in kwargs["input"]
        assert "https://example.test" not in kwargs["input"]
        assert json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))[
            "additionalProperties"
        ] is False
        return subprocess.CompletedProcess(command, 0, stdout=_evaluation_json(), stderr="progress")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    scout = Scout(platform="green", jd_text="AWS Terraform", url="https://example.test/private")
    evaluation = CodexClassifier(None, "Only use provided evidence.").classify(scout)
    assert evaluation.verdict == "KEEP"
    assert evaluation.summary == "AWS 职位与目标匹配。"
    assert len(calls) == 1


def test_codex_can_clean_one_markdown_fence():
    result = codex_module._parse_evaluation("```json\n" + _evaluation_json() + "\n```")
    assert result.verdict == "KEEP"


@pytest.mark.parametrize("output", ["not json", "{\"verdict\": \"BAD\"}"])
def test_codex_invalid_json_fails(monkeypatch, output):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    monkeypatch.setattr(
        codex_module.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=output, stderr=""),
    )
    with pytest.raises(CodexClassifierError, match="valid Evaluation JSON"):
        CodexClassifier(None, "rules").classify(Scout(platform="green", jd_text="AWS"))


def test_codex_timeout_fails(monkeypatch):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr=b"private details")

    monkeypatch.setattr(codex_module.subprocess, "run", timeout)
    with pytest.raises(CodexClassifierError, match="timed out") as caught:
        CodexClassifier(None, "rules").classify(Scout(platform="green", jd_text="AWS"))
    assert caught.value.stderr == "private details"
    assert "private details" not in str(caught.value)


def test_codex_nonzero_exit_preserves_stderr_without_logging(monkeypatch):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    monkeypatch.setattr(
        codex_module.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 9, stdout="", stderr="private details"),
    )
    with pytest.raises(CodexClassifierError, match="exit.*9") as caught:
        CodexClassifier("test-model", "rules").classify(Scout(platform="green", jd_text="AWS"))
    assert caught.value.stderr == "private details"
    assert "private details" not in str(caught.value)


def test_evaluate_codex_never_uses_browser_and_saves_metadata(tmp_path, monkeypatch):
    settings = Settings(tmp_path, None, None, classifier_provider="codex", codex_model="test-model")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "BrowserManager", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("browser constructed")
    ))
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "chatgpt")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=_evaluation_json(), stderr="")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    with Database(settings.db_path) as db:
        scout_id = db.save_scout(Scout(id="synthetic-1", platform="green", jd_text="AWS"))
        original_payload = db.conn.execute("SELECT payload FROM scouts WHERE id=?", (scout_id,)).fetchone()[0]
    assert cli.main(["evaluate", "--platform", "green", "--limit", "1"]) == 0
    assert len(commands) == 1
    assert commands[0][commands[0].index("--model") + 1] == "test-model"
    with Database(settings.db_path) as db:
        assert db.conn.execute("SELECT payload FROM scouts WHERE id=?", (scout_id,)).fetchone()[0] == original_payload
        row = db.conn.execute("SELECT provider, model_name, evaluated_at FROM evaluations").fetchone()
        assert row["provider"] == "codex"
        assert row["model_name"] == "test-model"
        assert datetime.fromisoformat(row["evaluated_at"])
    html = next(settings.output_path.glob("*.html")).read_text(encoding="utf-8")
    assert "Classifier/model: codex / test-model" in html


def test_api_key_login_is_rejected(monkeypatch):
    monkeypatch.setattr(codex_module, "codex_auth_mode", lambda: "api_key")
    monkeypatch.setattr(codex_module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("codex exec must not run")
    ))
    with pytest.raises(CodexClassifierError, match="ChatGPT login"):
        CodexClassifier(None, "rules").classify(Scout(platform="green", jd_text="AWS"))
