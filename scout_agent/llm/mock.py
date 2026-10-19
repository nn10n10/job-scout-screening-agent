from __future__ import annotations

import re

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.target_domain import assess_target_domain
from scout_agent.cloud_app_policy import INHOUSE, client_site_conflict
from scout_agent.search import detail_drop
from .base import BaseClassifier, MISSING_MESSAGE_CONCERN, validate_explanations


class MockClassifier(BaseClassifier):
    """Deterministic fixture-friendly approximation; never a substitute for review."""

    provider = "mock"
    model_name = "mock"

    def classify(self, scout: Scout) -> Evaluation:
        assessment = assess_target_domain(scout)
        text = " ".join(filter(None, [scout.job_title, scout.scout_text, scout.jd_text,
                                      scout.salary_text, scout.location_text])).lower()
        aws = "aws" in text
        terraform = "terraform" in text
        cicd = "ci/cd" in text
        docker = "docker" in text
        ecs = "ecs" in text
        kubernetes = "kubernetes" in text
        ses = client_site_conflict(text)
        inhouse = True if INHOUSE.search(text) else None
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
        if assessment.status != "TARGET_CONFIRMED":
            concerns.append("尚无法确认云平台或云相关开发属于岗位主要职责，或工作配属尚未明确。")
        if not scout.scout_text and scout.jd_text:
            concerns.append(MISSING_MESSAGE_CONCERN)
        hard_drop = detail_drop({"salary": scout.salary_text or "", "responsibilities": scout.jd_text or ""})
        if hard_drop or ses:
            verdict, confidence = "SKIP", 0.75
            summary = hard_drop or "岗位明确要求客先常駐或派遣配属。"
        elif assessment.status == "TARGET_CONFIRMED":
            verdict, confidence = "KEEP", 0.65
            reasons.append("JD 职责证据：" + assessment.evidence)
            summary = "岗位方向与目标匹配；这是 mock 初筛，仍需核对 JD 的实际职责。"
        else:
            verdict, confidence = "MAYBE", 0.4
            summary = "现有文本不足以确认匹配度，请人工复核。"
        return validate_explanations(Evaluation(
            verdict=verdict, confidence=confidence, summary=summary,
            reasons=reasons, concerns=concerns, inhouse=inhouse, ses=True if ses else None,
            client_site=True if ses else None,
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
