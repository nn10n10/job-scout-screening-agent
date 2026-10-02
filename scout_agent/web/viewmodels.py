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
    if filters.tier != "All":
        labels += (f"Tier {filters.tier}",)
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
    salary_display: str
    location: str | None
    remote_evidence: str | None
    annual_holidays: int | None
    priority_tier: str | None
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
    salary_display: str
    location: str | None
    remote_evidence: str | None
    annual_holidays: int | None
    priority_tier: str | None
    priority_evidence: str | None
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
    if result.priority is not None:
        return {
            "FULL_REMOTE": "Full Remote",
            "REMOTE_4_PLUS": "每周至少4天",
            "REMOTE_3": "每周至少3天",
            "REMOTE_1_2": "每周1–2天",
            "REMOTE_OCCASIONAL": "偶尔 Remote",
            "REMOTE_RANGE": "频率范围，最低不足3天",
            "REMOTE_UNKNOWN": "可 Remote，频率不明",
            "ONSITE": "明确出社",
            "UNKNOWN": "不明",
        }[result.priority.remote_level]
    evidence = []
    if result.evaluation.remote is True:
        evidence.append("Remote")
    if result.evaluation.hybrid is True:
        evidence.append("Hybrid")
    return " / ".join(evidence) or None


def _salary_display(result: StoredEvaluation) -> str:
    priority = result.priority
    if priority is not None:
        low, high = priority.salary_min, priority.salary_max
        if low is not None and high is not None:
            if low == high:
                return f"年収 {low // 10_000} 万円"
            return f"年収 {low // 10_000}–{high // 10_000} 万円"
        if low is not None:
            return f"年収 {low // 10_000} 万円以上"
        if high is not None:
            return f"年収 {high // 10_000} 万円以下"
    if result.salary:
        return _preview(result.salary.splitlines()[0], 75)
    return "不明"


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
            salary_display=_salary_display(result),
            location=result.location,
            remote_evidence=_remote_evidence(result),
            annual_holidays=result.priority.annual_holidays if result.priority else None,
            priority_tier=result.priority.priority_tier if result.priority else None,
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
        salary_display=_salary_display(result),
        location=result.location,
        remote_evidence=_remote_evidence(result),
        annual_holidays=result.priority.annual_holidays if result.priority else None,
        priority_tier=result.priority.priority_tier if result.priority else None,
        priority_evidence=result.priority.priority_evidence if result.priority else None,
        provider=result.provider,
        model_name=result.model_name,
        evaluated_at=result.evaluated_at.isoformat(sep=" ", timespec="seconds"),
        detail_url=_safe_detail_url(result.url),
    )


def search_cards(results, user_states=None):
    from scout_agent.green_discovery import Job
    cards = []
    for job, result in results:
        try:
            if job.platform == 'mynavi':
                from scout_agent.mynavi_search import MynaviSearchAdapter
                url = MynaviSearchAdapter().validate_job(job)
            elif job.platform == 'doda':
                from scout_agent.doda_search import DodaSearchAdapter
                url = DodaSearchAdapter().validate_job(job)
            elif job.platform == 'type':
                from scout_agent.type_search import TypeSearchAdapter
                url = TypeSearchAdapter().validate_job(job)
            elif job.platform == 'findy':
                from scout_agent.findy_search import FindySearchAdapter
                url = FindySearchAdapter().validate_job(job)
            elif job.platform == 'lapras':
                from scout_agent.lapras_search import LaprasSearchAdapter
                url = LaprasSearchAdapter().validate_job(job)
            elif job.platform == 'forkwell':
                from scout_agent.forkwell_discovery import ForkwellSearchAdapter
                url = ForkwellSearchAdapter().validate_job(job)
            else:
                url = Job.from_url(job.url, {}).url
        except (ValueError, TypeError):
            url = None
        def preview(value, limit=72):
            text = ' '.join(str(value or '').split())
            return text if len(text) <= limit else text[:limit - 1] + '…'
        cards.append(dict(
            job_id=job.job_id, verdict=result.verdict, platform_label={'green': 'Green', 'forkwell': 'Forkwell', 'lapras': 'LAPRAS', 'findy': 'Findy', 'type': 'Type', 'doda': 'doda', 'mynavi': 'マイナビ転職'}.get(job.platform, 'Green') if url else '未知平台',
            user_status=(user_states or {}).get(job.job_id, 'ACTIVE'),
            company=job.fields.get('company'), title=job.fields.get('title'),
            salary_preview=preview(job.fields.get('salary'), 40),
            location_preview=preview(job.fields.get('location')),
            sources=job.matched_keywords,
            source_preview=preview('、'.join(job.matched_keywords)),
            summary_preview=preview(result.summary), summary=result.summary,
            reasons=result.reasons, concerns=result.concerns, url=url,
            **{key: job.fields.get(key) for key in
               ('responsibilities', 'required', 'preferred', 'technology', 'remote')}
        ))
    return sorted(cards, key=lambda card: {'TARGET': 0, 'POSSIBLE': 1, 'DROP': 2}[card['verdict']])
