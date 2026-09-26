from pathlib import Path

import pytest

from scout_agent.llm import gemini as gemini_module
from scout_agent.llm.base import MISSING_MESSAGE_CONCERN, validate_explanations
from scout_agent.llm.gemini import GeminiClassifier
from scout_agent.llm.mock import MockClassifier
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout


def test_explanations_use_chinese_while_names_and_terms_stay_original():
    scout = Scout(
        platform="green", company_name="株式会社テスト", job_title="Cloud Engineer",
        jd_text="AWS / Terraform", scout_text=None,
    )
    evaluation = MockClassifier().classify(scout)
    assert evaluation.verdict == "KEEP"
    assert MISSING_MESSAGE_CONCERN in evaluation.concerns
    assert "Scout message unavailable" not in " ".join(evaluation.concerns)
    assert scout.company_name == "株式会社テスト"
    assert scout.job_title == "Cloud Engineer"
    assert any("AWS" in reason for reason in evaluation.reasons)
    assert validate_explanations(evaluation) is evaluation


@pytest.mark.parametrize(
    "field,changes",
    [
        ("summary", {"summary": "AWS matches the target."}),
        ("reasons", {"reasons": ["Good salary."]}),
        ("concerns", {"concerns": ["Remote policy is unclear."]}),
    ],
)
def test_english_only_explanations_fail(field, changes):
    evaluation = Evaluation(
        verdict="MAYBE", confidence=0.5, summary="信息不足，需人工判断。",
        reasons=["JD 提及 AWS。"], concerns=["薪资尚不明确。"],
    ).model_copy(update=changes)
    with pytest.raises(ValueError, match=field):
        validate_explanations(evaluation)


def test_gemini_prompt_and_structured_output_require_chinese(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {
                "parsed": Evaluation(
                    verdict="KEEP", confidence=0.8,
                    summary="株式会社テスト的 Cloud Engineer 职位符合 AWS 方向。",
                    reasons=["JD 明确提及 Terraform。"], concerns=[],
                ),
                "text": None,
            })()

    monkeypatch.setattr(gemini_module.genai, "Client", lambda api_key: type(
        "FakeClient", (), {"models": FakeModels()}
    )())
    rules = (Path(gemini_module.__file__).parents[1] / "prompts" / "screening_rules.txt").read_text(encoding="utf-8")
    scout = Scout(platform="green", company_name="株式会社テスト", job_title="Cloud Engineer", jd_text="AWS Terraform")
    evaluation = GeminiClassifier("placeholder", "gemini-test", rules).classify(scout)
    assert evaluation.verdict == "KEEP"
    assert "简体中文" in captured["contents"]
    assert "简体中文" in captured["config"].system_instruction
    assert "株式会社テスト" in captured["contents"]
    assert "Cloud Engineer" in captured["contents"]


def test_gemini_rejects_english_explanation(monkeypatch):
    class FakeModels:
        def generate_content(self, **kwargs):
            return type("Response", (), {
                "parsed": Evaluation(verdict="MAYBE", confidence=0.5, summary="Needs review."),
                "text": None,
            })()

    monkeypatch.setattr(gemini_module.genai, "Client", lambda api_key: type(
        "FakeClient", (), {"models": FakeModels()}
    )())
    with pytest.raises(ValueError, match="summary"):
        GeminiClassifier("placeholder", "gemini-test", "用简体中文说明").classify(
            Scout(platform="green", jd_text="AWS")
        )
