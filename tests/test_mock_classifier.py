import pytest

from scout_agent.llm.mock import MockClassifier
from scout_agent.models.scout import Scout


def test_mock_keeps_matching_fixture():
    scout = Scout(platform="generic", job_title="AWS Engineer", jd_text="Terraform を使用。", salary_text="年収500万円〜")
    result = MockClassifier().classify(scout)
    assert result.verdict == "KEEP"
    assert result.aws is True
    assert result.terraform is True
    assert result.salary_min_jpy == 5000000
    assert result.ses is None
    assert result.oncall is None


def test_mock_uses_maybe_when_evidence_is_missing():
    result = MockClassifier().classify(Scout(platform="generic", job_title="Engineer"))
    assert result.verdict == "MAYBE"
    assert result.inhouse is None
    assert result.salary_min_jpy is None


def test_mock_flags_explicit_ses():
    result = MockClassifier().classify(Scout(platform="generic", jd_text="SES・客先常駐の仕事です。"))
    assert result.verdict == "SKIP"
    assert result.ses is True
    assert result.client_site is True


@pytest.mark.parametrize(
    "job_title,jd_text",
    [
        ("Web/Application Engineer", "AWS と Terraform を使った Web アプリ開発。"),
        ("Backend Engineer", "主な業務は API 開発。ECS を時々保守。AWS Terraform CI/CD。"),
        ("Product Engineer", "SRE チームと協業。AWS Terraform CI/CD。"),
        ("Engineering Leader", "開発チーム管理が中心。AWS Terraform CI/CD も一部担当。"),
        ("Presales", "AWS Terraform に関する提案・営業支援。"),
        ("Cloud Engineer / Backend Engineer", "AWS Terraform CI/CD。"),
        ("Cloud Engineer", "主な業務は Webアプリ開発。AWS Terraform CI/CD。"),
    ],
)
def test_mock_does_not_keep_secondary_cloud_work(job_title, jd_text):
    result = MockClassifier().classify(Scout(
        platform="generic", job_title=job_title, jd_text=jd_text,
        salary_text="年収500万円〜",
    ))
    assert result.verdict == "MAYBE"
    assert any("主要职责" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    "job_title",
    ["Cloud Engineer", "Infrastructure Engineer", "Platform Engineer", "DevOps Engineer", "SRE", "クラウド基盤エンジニア"],
)
def test_mock_keeps_clear_target_roles_without_requiring_keywords(job_title):
    result = MockClassifier().classify(Scout(platform="generic", job_title=job_title))
    assert result.verdict == "KEEP"
