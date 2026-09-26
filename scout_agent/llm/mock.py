from __future__ import annotations

import re

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from .base import BaseClassifier, MISSING_MESSAGE_CONCERN, validate_explanations


_CLOUD_ROLE = re.compile(
    r"\b(?:cloud(?:[- /]native)?(?:[- /]infrastructure)?|infrastructure|platform|devops|aws)\s+"
    r"(?:engineer|architect)\b|\bsre\b|"
    r"クラウド(?:インフラ|基盤)?エンジニア|インフラエンジニア|"
    r"プラットフォームエンジニア|基盤エンジニア",
    re.IGNORECASE,
)
_OTHER_PRIMARY_ROLE = re.compile(
    r"\b(?:web|application|backend|back-end|frontend|front-end|full-stack|fullstack|product)\s+"
    r"(?:engineer|developer)\b|\b(?:engineering (?:leader|manager)|project manager|"
    r"product manager|presales|pre-sales|tech lead|team lead|pm)\b|"
    r"Webアプリ|アプリケーション|バックエンド|フロントエンド|"
    r"プロダクトエンジニア|開発リーダー|マネージャー|プリセールス",
    re.IGNORECASE,
)
_OTHER_PRIMARY_DUTY = re.compile(
    r"(?:主な業務|主業務|主要業務|メイン業務|仕事内容|主要职责|核心职责|"
    r"primary responsibilities|main responsibilities).{0,80}"
    r"(?:webアプリ(?:ケーション)?開発|バックエンド開発|フロントエンド開発|"
    r"顧客向け業務システム|顧客業務システム|プリセールス|"
    r"\b(?:web|application|backend|frontend|front-end|back-end)\s+(?:development|engineering)\b|"
    r"\b(?:presales|pre-sales|people management|project management)\b)",
    re.IGNORECASE | re.DOTALL,
)


class MockClassifier(BaseClassifier):
    """Deterministic fixture-friendly approximation; never a substitute for review."""

    provider = "mock"
    model_name = "mock"

    def classify(self, scout: Scout) -> Evaluation:
        title = scout.job_title or ""
        # Mock cannot infer duty proportions from arbitrary JD prose. A clearly
        # target-role title is sufficient for a provisional KEEP only when it
        # does not also name a different primary role or conflict with explicit
        # JD duties; ambiguous roles stay MAYBE.
        primary_cloud_role = (
            bool(_CLOUD_ROLE.search(title))
            and not _OTHER_PRIMARY_ROLE.search(title)
            and not _OTHER_PRIMARY_DUTY.search(scout.jd_text or "")
        )
        text = " ".join(filter(None, [scout.job_title, scout.scout_text, scout.jd_text,
                                      scout.salary_text, scout.location_text])).lower()
        aws = "aws" in text
        terraform = "terraform" in text
        cicd = "ci/cd" in text
        docker = "docker" in text
        ecs = "ecs" in text
        kubernetes = "kubernetes" in text
        ses = bool(re.search(r"\bses\b|客先常駐|客先常驻", text))
        inhouse = True if any(term in text for term in ("自社サービス", "自社製品", "内製")) else None
        remote = True if any(term in text for term in ("リモート", "remote", "在宅")) else None
        hybrid = True if "ハイブリッド" in text or "hybrid" in text else None
        salary_match = re.search(r"(?:年収)?\s*(\d{3,4})\s*万円", (scout.salary_text or ""))
        salary_min = int(salary_match.group(1)) * 10000 if salary_match else None
        reasons: list[str] = []
        concerns: list[str] = []
        if aws:
            reasons.append("职位文本明确提及 AWS。")
        if terraform:
            reasons.append("职位文本明确提及 Terraform。")
        if inhouse:
            reasons.append("职位文本提及自社服务或内製。")
        if salary_min is not None and salary_min >= 4500000:
            reasons.append("薪资下限达到目标。")
        if ses:
            concerns.append("职位文本提及 SES 或客先常駐。")
        if salary_min is None:
            concerns.append("薪资下限未明确。")
        if inhouse is None and not ses:
            concerns.append("无法判断自社/SES 性质。")
        if not primary_cloud_role:
            concerns.append("尚无法确认 Cloud/Infrastructure/Platform/DevOps/SRE 属于岗位主要职责。")
        if not scout.scout_text and scout.jd_text:
            concerns.append(MISSING_MESSAGE_CONCERN)
        if ses:
            verdict, confidence = "SKIP", 0.75
            summary = "文本显示 SES/客先常駐，建议降低优先级。"
        elif primary_cloud_role and (salary_min is None or salary_min >= 4500000):
            verdict, confidence = "KEEP", 0.65
            reasons.append("职位名称明确指向 Cloud/Infrastructure/Platform/DevOps/SRE 方向。")
            summary = "岗位方向与目标匹配；这是 mock 初筛，仍需核对 JD 的实际职责。"
        else:
            verdict, confidence = "MAYBE", 0.4
            summary = "现有文本不足以确认匹配度，请人工复核。"
        return validate_explanations(Evaluation(
            verdict=verdict, confidence=confidence, summary=summary,
            reasons=reasons, concerns=concerns, inhouse=inhouse, ses=True if ses else None,
            client_site=True if "客先常駐" in text or "客先常驻" in text else None,
            aws=True if aws else None, terraform=True if terraform else None,
            cicd=True if cicd else None, docker=True if docker else None,
            ecs=True if ecs else None, kubernetes=True if kubernetes else None,
            kubernetes_required=(
                True if re.search(r"kubernetes.{0,30}必須|必須.{0,30}kubernetes", text)
                else False if re.search(r"kubernetes.{0,30}歓迎|歓迎.{0,30}kubernetes", text)
                else None
            ),
            remote=remote, hybrid=hybrid,
            japanese_primary=True if "日本語で業務" in text else None,
            english_primary=None, oncall=None, salary_min_jpy=salary_min,
            casual_interview_required=None,
        ))
