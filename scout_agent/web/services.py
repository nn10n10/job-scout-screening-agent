from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout_agent.models.evaluation import Evaluation
from scout_agent.storage.db import Database


SUPPORTED_PLATFORMS = ("green", "type", "doda", "mynavi")
PLATFORMS = ("All", *SUPPORTED_PLATFORMS)
VERDICTS = ("KEEP,MAYBE", "KEEP", "MAYBE", "SKIP", "All")
PROVIDERS = ("Final", "codex", "local", "mock", "All")
DAY_RANGES = ("1", "7", "14", "30", "all")
RESULT_LIMIT = 100


@dataclass(frozen=True)
class DashboardFilters:
    q: str = ""
    platform: str = "All"
    verdict: str = "KEEP,MAYBE"
    provider: str = "Final"
    days: str = "7"

    def __post_init__(self) -> None:
        object.__setattr__(self, "q", self.q.strip())
        if self.provider.lower() in {"all", "final"}:
            object.__setattr__(self, "provider", self.provider.title())
        if self.days.lower() == "all":
            object.__setattr__(self, "days", "all")
        if len(self.q) > 200:
            raise ValueError("keyword is too long")
        if self.platform not in PLATFORMS or self.verdict not in VERDICTS \
                or self.provider not in PROVIDERS or self.days not in DAY_RANGES:
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


# Select only display fields from Scout JSON. Private Scout messages and JD text
# never leave SQLite for the WebUI, even on the detail route.
_RESULT_COLUMNS = (
    "SELECT s.id AS scout_id,s.platform,"
    "json_extract(s.payload,'$.company_name') AS company,"
    "json_extract(s.payload,'$.job_title') AS job_title,"
    "json_extract(s.payload,'$.salary_text') AS salary,"
    "json_extract(s.payload,'$.location_text') AS location,"
    "s.url,e.payload AS evaluation_payload,e.provider,e.model_name,"
    f"{_RECEIVED_DATE} AS received_date,"
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
    )


def _escaped_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


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
        # Real classifier providers (including future ones) and local hard-rule
        # decisions remain visible. The latter normally have only SKIP verdicts.
        clauses.append("e.provider NOT IN ('mock','legacy')")
    elif filters.provider != "All":
        clauses.append("e.provider=?")
        params.append(filters.provider)
    if filters.days != "all":
        clauses.append(f"substr({_RECEIVED_DATE},1,10)>=?")
        params.append(((now or datetime.now()).date() - timedelta(days=int(filters.days))).isoformat())
    return " AND ".join(clauses), params


def search_evaluations(
    db_path: Path, filters: DashboardFilters, *, now: datetime | None = None,
) -> list[StoredEvaluation]:
    """Apply every filter and the 100-row limit in parameterized SQLite SQL."""
    if not db_path.is_file():
        return []
    where, params = _where_clause(filters, now)
    sql = (
        f"{_RESULT_COLUMNS} WHERE {where} "
        f"ORDER BY {_RECEIVED_DATE} DESC,"
        "COALESCE(e.evaluated_at,e.created_at) DESC,e.id DESC LIMIT ?"
    )
    params.append(RESULT_LIMIT)
    with Database(db_path, read_only=True) as db:
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
        row = db.conn.execute(
            f"{_RESULT_COLUMNS} WHERE s.id=?", (scout_id,),
        ).fetchone()
    return _from_row(row) if row is not None else None
