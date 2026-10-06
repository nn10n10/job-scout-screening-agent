from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout_agent.local_reprocess import reprocess_stored_scout
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.priority import PriorityAssessment, assess_priority
from scout_agent.storage.db import Database
from scout_agent.target_domain import assess_target_domain
from scout_agent.workflow_gate import assess_workflow


SUPPORTED_PLATFORMS = ("green", "type", "doda", "mynavi")
PLATFORMS = ("All", *SUPPORTED_PLATFORMS)
VERDICTS = ("KEEP,MAYBE", "KEEP", "MAYBE", "SKIP", "All")
PROVIDERS = ("Final", "codex", "local", "mock", "All")
DAY_RANGES = ("1", "7", "14", "30", "all")
TIER_OPTIONS = ("All", "S", "A", "B", "C")
RESULT_LIMIT = 100
PAGE_SIZES = (10, 20, 50, 100)


@dataclass(frozen=True)
class DashboardFilters:
    q: str = ""
    platform: str = "All"
    verdict: str = "KEEP,MAYBE"
    provider: str = "Final"
    days: str = "7"
    tier: str = "All"

    def __post_init__(self) -> None:
        object.__setattr__(self, "q", self.q.strip())
        if self.provider.lower() in {"all", "final"}:
            object.__setattr__(self, "provider", self.provider.title())
        if self.days.lower() == "all":
            object.__setattr__(self, "days", "all")
        if len(self.q) > 200:
            raise ValueError("keyword is too long")
        if self.platform not in PLATFORMS or self.verdict not in VERDICTS \
                or self.provider not in PROVIDERS or self.days not in DAY_RANGES \
                or self.tier not in TIER_OPTIONS:
            raise ValueError("invalid dashboard filter")


@dataclass(frozen=True)
class StoredEvaluation:
    scout_id: int
    platform: str
    company: str | None
    job_title: str | None
    salary: str | None
    location: str | None
    url: str | None
    evaluation: Evaluation
    provider: str
    model_name: str
    evaluated_at: datetime
    received_date: str | None
    priority: PriorityAssessment | None


@dataclass(frozen=True)
class PlatformCount:
    platform: str
    count: int


@dataclass(frozen=True)
class DashboardSummary:
    total: int
    keep: int
    maybe: int
    skip: int
    platforms: tuple[PlatformCount, ...]


# The date used for dashboard scope and sorting; do not substitute evaluation
# time for a missing receipt date. doda supplies an age at first observation.
_RECEIVED_DATE = (
    "COALESCE(json_extract(s.payload,'$.received_at'),"
    "json_extract(s.payload,'$.received_on'),"
    "CASE WHEN s.platform='doda' AND d.age_days IS NOT NULL "
    "THEN date(d.created_at, '-' || d.age_days || ' days') END)"
)


# Select only display fields from Scout JSON. The local-rule SQL callback may
# inspect a stored JD, but private Scout text/JD never enters returned rows.
_RESULT_COLUMNS = (
    "SELECT s.id AS scout_id,s.platform,"
    "json_extract(s.payload,'$.company_name') AS company,"
    "json_extract(s.payload,'$.job_title') AS job_title,"
    "json_extract(s.payload,'$.salary_text') AS salary,"
    "json_extract(s.payload,'$.location_text') AS location,"
    "s.url,e.payload AS evaluation_payload,e.provider,e.model_name,"
    f"{_RECEIVED_DATE} AS received_date,"
    "priority_payload(s.payload) AS priority_payload,"
    "COALESCE(e.evaluated_at,e.created_at) AS evaluated_at "
    "FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
    "LEFT JOIN doda_list_items d ON s.platform='doda' AND d.external_id=s.external_id"
)


def _from_row(row: Any) -> StoredEvaluation:
    return StoredEvaluation(
        scout_id=int(row["scout_id"]), platform=row["platform"],
        company=row["company"], job_title=row["job_title"],
        salary=row["salary"], location=row["location"], url=row["url"],
        evaluation=Evaluation.model_validate_json(row["evaluation_payload"]),
        provider=row["provider"], model_name=row["model_name"],
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        received_date=row["received_date"],
        priority=PriorityAssessment.from_json(row["priority_payload"]) if row["priority_payload"] else None,
    )


def _escaped_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _current_local_candidate(platform: str, payload: str) -> int:
    """Use the existing local reprocess rules, not an old eligibility marker.

    No stored rule result is permanently authoritative: rules may change after
    the last scan or reprocess. Missing/invalid complete JDs are not verified
    classifier candidates. Green has no detail local-reprocess rule today.
    """
    if platform == "green":
        return 1
    try:
        scout = Scout.model_validate_json(payload)
        return int(reprocess_stored_scout(scout).decision == "classifier_candidate")
    except (TypeError, ValueError):
        return 0


def _target_domain_confirmed(payload: str) -> int:
    """Only an evidenced main-duty target role may appear as Final KEEP/MAYBE."""
    try:
        scout = Scout.model_validate_json(payload)
        return int(assess_target_domain(scout).status == "TARGET_CONFIRMED")
    except (TypeError, ValueError):
        return 0


def _workflow_not_rejected(payload: str) -> int:
    """Exclude only explicitly mandatory pre-selection Casual interviews."""
    try:
        scout = Scout.model_validate_json(payload)
        return int(assess_workflow(scout).status != "WORKFLOW_REJECTED")
    except (TypeError, ValueError):
        return 0


def _priority_payload(payload: str) -> str | None:
    try:
        priority = assess_priority(Scout.model_validate_json(payload))
        return priority.to_json() if priority else None
    except (TypeError, ValueError):
        return None


def _register_readonly_functions(db: Database) -> None:
    db.conn.create_function("current_local_candidate", 2, _current_local_candidate)
    db.conn.create_function("target_domain_confirmed", 1, _target_domain_confirmed)
    db.conn.create_function("workflow_not_rejected", 1, _workflow_not_rejected)
    db.conn.create_function("priority_payload", 1, _priority_payload)


def _where_clause(filters: DashboardFilters, now: datetime | None) -> tuple[str, list[str | int]]:
    """Keep the result and aggregate queries on exactly the same SQL scope."""
    clauses: list[str] = ["s.platform IN (?,?,?,?)"]
    params: list[str | int] = []
    params.extend(SUPPORTED_PLATFORMS)
    if filters.q:
        clauses.append(
            "(COALESCE(json_extract(s.payload,'$.company_name'),'') LIKE ? ESCAPE '\\' "
            "OR COALESCE(json_extract(s.payload,'$.job_title'),'') LIKE ? ESCAPE '\\')"
        )
        term = _escaped_like(filters.q)
        params.extend((term, term))
    if filters.platform != "All":
        clauses.append("s.platform=?")
        params.append(filters.platform)
    if filters.verdict == "KEEP,MAYBE":
        clauses.append("json_extract(e.payload,'$.verdict') IN ('KEEP','MAYBE')")
    elif filters.verdict != "All":
        clauses.append("json_extract(e.payload,'$.verdict')=?")
        params.append(filters.verdict)
    if filters.provider == "Final":
        # Mock is a test result, and legacy has no verified provider identity.
        # A real classifier verdict is final only while the *current* local
        # rules still consider this Scout eligible. Historical evaluations stay
        # stored and remain available under an explicit provider or detail URL.
        # Conversely, a local SKIP is final only while the current rules skip.
        clauses.append("e.provider NOT IN ('mock','legacy')")
        clauses.append(
            "((e.provider='local' AND json_extract(e.payload,'$.verdict')='SKIP' "
            "AND current_local_candidate(s.platform,s.payload)=0) "
            "OR (e.provider<>'local' AND current_local_candidate(s.platform,s.payload)=1 "
            "AND (json_extract(e.payload,'$.verdict')='SKIP' "
            "OR (target_domain_confirmed(s.payload)=1 "
            "AND workflow_not_rejected(s.payload)=1))))"
        )
    elif filters.provider != "All":
        clauses.append("e.provider=?")
        params.append(filters.provider)
    if filters.tier != "All":
        clauses.append("json_extract(priority_payload(s.payload),'$.priority_tier')=?")
        params.append(filters.tier)
    if filters.provider == "Final":
        # A known annual upper bound below ¥4.5m is not a default Final hit.
        # Unknown salary and ranges spanning ¥4.5m are not excluded.
        clauses.append(
            "(json_extract(e.payload,'$.verdict')='SKIP' OR "
            "COALESCE(json_extract(priority_payload(s.payload),'$.salary_below_target'),0)=0)"
        )
    if filters.days != "all":
        clauses.append(f"substr({_RECEIVED_DATE},1,10)>=?")
        params.append(((now or datetime.now()).date() - timedelta(days=int(filters.days))).isoformat())
    return " AND ".join(clauses), params


def search_evaluations(
    db_path: Path, filters: DashboardFilters, *, now: datetime | None = None,
    page: int = 1, page_size: int = RESULT_LIMIT,
) -> list[StoredEvaluation]:
    """Apply filters and pagination in SQL."""
    if page < 1 or page_size not in PAGE_SIZES:
        raise ValueError("invalid pagination")
    if not db_path.is_file():
        return []
    where, params = _where_clause(filters, now)
    tier_sort = (
        "CASE json_extract(priority_payload(s.payload),'$.priority_tier') "
        "WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,"
        if filters.provider == "Final" else ""
    )
    sql = (
        f"{_RESULT_COLUMNS} WHERE {where} "
        f"ORDER BY {tier_sort}{_RECEIVED_DATE} DESC,"
        "COALESCE(e.evaluated_at,e.created_at) DESC,e.id DESC LIMIT ? OFFSET ?"
    )
    params.extend((page_size, (page - 1) * page_size))
    with Database(db_path, read_only=True) as db:
        _register_readonly_functions(db)
        rows = db.conn.execute(sql, params).fetchall()
    return [_from_row(row) for row in rows]


def summarize_evaluations(
    db_path: Path, filters: DashboardFilters, *, now: datetime | None = None,
) -> DashboardSummary:
    """Aggregate the entire filtered SQL result, independently of card LIMIT."""
    if not db_path.is_file():
        return DashboardSummary(0, 0, 0, 0, ())
    where, params = _where_clause(filters, now)
    sql = (
        "SELECT s.platform,COUNT(*) AS total,"
        "SUM(CASE WHEN json_extract(e.payload,'$.verdict')='KEEP' THEN 1 ELSE 0 END) AS keep_count,"
        "SUM(CASE WHEN json_extract(e.payload,'$.verdict')='MAYBE' THEN 1 ELSE 0 END) AS maybe_count,"
        "SUM(CASE WHEN json_extract(e.payload,'$.verdict')='SKIP' THEN 1 ELSE 0 END) AS skip_count "
        "FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
        "LEFT JOIN doda_list_items d ON s.platform='doda' AND d.external_id=s.external_id "
        f"WHERE {where} GROUP BY s.platform ORDER BY total DESC,s.platform"
    )
    with Database(db_path, read_only=True) as db:
        _register_readonly_functions(db)
        rows = db.conn.execute(sql, params).fetchall()
    return DashboardSummary(
        total=sum(row["total"] for row in rows),
        keep=sum(row["keep_count"] for row in rows),
        maybe=sum(row["maybe_count"] for row in rows),
        skip=sum(row["skip_count"] for row in rows),
        platforms=tuple(PlatformCount(row["platform"], row["total"]) for row in rows),
    )


def get_evaluation(db_path: Path, scout_id: int) -> StoredEvaluation | None:
    if not db_path.is_file():
        return None
    with Database(db_path, read_only=True) as db:
        _register_readonly_functions(db)
        row = db.conn.execute(
            f"{_RESULT_COLUMNS} WHERE s.id=?", (scout_id,),
        ).fetchone()
    return _from_row(row) if row is not None else None
