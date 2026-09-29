from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from .services import DashboardFilters, StoredEvaluation


@dataclass(frozen=True)
class DashboardScope:
    labels: tuple[str, ...]
    total_label: str
    show_skip: bool


def dashboard_scope(filters: DashboardFilters) -> DashboardScope:
    verdict = "KEEP/MAYBE" if filters.verdict == "KEEP,MAYBE" else filters.verdict
    period = "全部时间" if filters.days == "all" else f"最近{filters.days}天"
    platform = "All platforms" if filters.platform == "All" else filters.platform
    labels = (filters.provider, verdict, period, platform)
    if filters.q:
        labels += (f"关键词：{filters.q}",)
    return DashboardScope(
        labels=labels,
        total_label="Final results" if filters.provider == "Final" else "筛选结果",
        show_skip=filters.verdict in {"SKIP", "All"},
    )


@dataclass(frozen=True)
class EvaluationCard:
    scout_id: int
    verdict: str
    platform: str
    company: str | None
    job_title: str | None
    salary: str | None
    location: str | None
    remote_evidence: str | None
    provider: str
    evaluated_at: str
    received_date: str | None
    summary: str
    concerns_preview: tuple[str, ...]
    detail_url: str | None


@dataclass(frozen=True)
class EvaluationDetail:
    verdict: str
    platform: str
    company: str | None
    job_title: str | None
    confidence: float
    summary: str
    reasons: tuple[str, ...]
    concerns: tuple[str, ...]
    salary: str | None
    location: str | None
    remote_evidence: str | None
    provider: str
    model_name: str
    evaluated_at: str
    detail_url: str | None


def _safe_detail_url(url: str | None) -> str | None:
    if not url or any(char.isspace() or ord(char) < 32 or char == "\\" for char in url):
        return None
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if parsed.username or parsed.password:
        return None
    sensitive_keys = {
        "token", "access_token", "refresh_token", "secret", "auth", "session",
        "password", "cookie", "credential", "api_key", "access_key", "signature",
    }
    if any(key.lower() in sensitive_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    return url


def _remote_evidence(result: StoredEvaluation) -> str | None:
    evidence = []
    if result.evaluation.remote is True:
        evidence.append("Remote")
    if result.evaluation.hybrid is True:
        evidence.append("Hybrid")
    return " / ".join(evidence) or None


def _preview(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit - 1].rstrip() + "…"


def evaluation_cards(results: list[StoredEvaluation]) -> list[EvaluationCard]:
    return [
        EvaluationCard(
            scout_id=result.scout_id,
            verdict=result.evaluation.verdict,
            platform=result.platform,
            company=result.company,
            job_title=result.job_title,
            salary=result.salary,
            location=result.location,
            remote_evidence=_remote_evidence(result),
            provider=result.provider,
            evaluated_at=result.evaluated_at.strftime("%Y-%m-%d %H:%M"),
            received_date=result.received_date[:10] if result.received_date else None,
            summary=_preview(result.evaluation.summary, 140),
            concerns_preview=tuple(_preview(item, 95) for item in result.evaluation.concerns[:2]),
            detail_url=_safe_detail_url(result.url),
        )
        for result in results
    ]


def evaluation_detail(result: StoredEvaluation) -> EvaluationDetail:
    return EvaluationDetail(
        verdict=result.evaluation.verdict,
        platform=result.platform,
        company=result.company,
        job_title=result.job_title,
        confidence=result.evaluation.confidence,
        summary=result.evaluation.summary,
        reasons=tuple(result.evaluation.reasons),
        concerns=tuple(result.evaluation.concerns),
        salary=result.salary,
        location=result.location,
        remote_evidence=_remote_evidence(result),
        provider=result.provider,
        model_name=result.model_name,
        evaluated_at=result.evaluated_at.isoformat(sep=" ", timespec="seconds"),
        detail_url=_safe_detail_url(result.url),
    )
