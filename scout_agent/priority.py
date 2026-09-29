"""Evidence-only priority tiers for confirmed target-domain Scouts.

This module never changes an Evaluation. Annual salary amounts are stored as
JPY integers; missing evidence stays None/UNKNOWN. REMOTE_RANGE is an extra
remote level for explicit ranges whose minimum is below three days per week.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Literal

from scout_agent.models.scout import Scout
from scout_agent.target_domain import assess_target_domain


PriorityTier = Literal["S", "A", "B", "C"]
RemoteLevel = Literal[
    "FULL_REMOTE", "REMOTE_4_PLUS", "REMOTE_3", "REMOTE_1_2",
    "REMOTE_OCCASIONAL", "REMOTE_RANGE", "REMOTE_UNKNOWN", "ONSITE", "UNKNOWN",
]


@dataclass(frozen=True)
class PriorityAssessment:
    priority_tier: PriorityTier
    remote_level: RemoteLevel
    annual_holidays: int | None
    salary_min: int | None
    salary_max: int | None
    priority_evidence: str
    salary_below_target: bool
    remote_evidence: str | None = None
    holidays_evidence: str | None = None
    salary_evidence: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> PriorityAssessment:
        return cls(**json.loads(payload))


_REMOTE_WORD = r"(?:リモート(?:ワーク)?|在宅(?:勤務)?|テレワーク|remote)"
_WEEKLY_BEFORE = re.compile(
    rf"週(?:に|平均)?\s*([1-7１-７])\s*(?:[～〜~\-－]\s*([1-7１-７]))?\s*日?\s*(以上|程度)?\s*"
    rf"(?:の|は|で)?\s*{_REMOTE_WORD}", re.I,
)
_WEEKLY_AFTER = re.compile(
    rf"{_REMOTE_WORD}[^。\n]{{0,18}}?週(?:に|平均)?\s*([1-7１-７])\s*"
    rf"(?:[～〜~\-－]\s*([1-7１-７]))?\s*日?\s*(以上|程度)?", re.I,
)
_FULL = re.compile(
    r"原則フルリモート(?:勤務|ワーク)?|"
    r"フルリモート(?:勤務|ワーク|制|体制)|"
    r"完全(?:在宅|リモート)(?:勤務|ワーク|制|体制)|"
    r"full[ -]?remote(?: work)?",
    re.I,
)
_REMOTE_ANY = re.compile(_REMOTE_WORD, re.I)
_ONSITE = re.compile(r"原則出社|完全出社|フル出社|リモート不可|在宅勤務不可|常時出社")
_OCCASIONAL = re.compile(rf"月\s*[1-4１-４]\s*回[^。\n]{{0,12}}{_REMOTE_WORD}", re.I)
_HOLIDAYS = re.compile(r"(?:年間休日|年休)\s*(\d{3})\s*日")
_ANNUAL_SALARY = re.compile(r"年収(?!\s*(?:UP|アップ|例|モデル))|年俸", re.I)
_MANYEN_RANGE = re.compile(
    r"(?<!\d)(\d{3,4}(?:,\d{3})?)\s*(?:万(?:円)?)?\s*[～〜~\-－]\s*"
    r"(\d{3,4}(?:,\d{3})?)\s*万(?:円)?"
)
_MANYEN_SINGLE = re.compile(r"(?<!\d)(\d{3,4}(?:,\d{3})?)\s*万(?:円)?\s*(以上|以下|から|～)?")


def _brief(text: str) -> str:
    return " ".join(text.split())[:90]


def _digit(value: str) -> int:
    return int(value.translate(str.maketrans("１２３４５６７", "1234567")))


def _remote_from_text(text: str, *, allow_full: bool) -> tuple[RemoteLevel, str | None]:
    if not text:
        return "UNKNOWN", None
    if allow_full:
        for match in _FULL.finditer(text):
            end = min([position for position in (
                text.find("。", match.end()), text.find("\n", match.end()), len(text),
            ) if position >= 0])
            clause = text[match.start():end]
            if not re.search(r"可|可能|相談|希望|案件|一部|次第|応じて|OK|選択|ではない|ではありません", clause, re.I):
                return "FULL_REMOTE", _brief(match.group(0))
    for pattern in (_WEEKLY_BEFORE, _WEEKLY_AFTER):
        match = pattern.search(text)
        if match:
            low = _digit(match.group(1))
            high = _digit(match.group(2)) if match.group(2) else low
            qualifier = match.group(3)
            if low > high:
                return "REMOTE_UNKNOWN", _brief(match.group(0))
            if qualifier == "程度" or qualifier == "以上" and low < 3:
                level: RemoteLevel = "REMOTE_RANGE"
            elif low >= 4:
                level: RemoteLevel = "REMOTE_4_PLUS"
            elif low >= 3:
                level = "REMOTE_3"
            elif high <= 2:
                level = "REMOTE_1_2"
            else:
                level = "REMOTE_RANGE"
            return level, _brief(match.group(0))
    occasional = _OCCASIONAL.search(text)
    if occasional:
        return "REMOTE_OCCASIONAL", _brief(occasional.group(0))
    onsite = _ONSITE.search(text)
    if onsite:
        return "ONSITE", _brief(onsite.group(0))
    remote = _REMOTE_ANY.search(text) or _FULL.search(text)
    if remote:
        return "REMOTE_UNKNOWN", _brief(remote.group(0))
    return "UNKNOWN", None


def _remote(scout: Scout) -> tuple[RemoteLevel, str | None]:
    # Full Remote requires JD evidence; a headline saying "フルリモート可"
    # alone is not enough. Other exact weekly terms may appear in the title.
    from_jd = _remote_from_text(scout.jd_text or "", allow_full=True)
    if from_jd[0] not in {"UNKNOWN", "REMOTE_UNKNOWN"}:
        return from_jd
    from_title = _remote_from_text(scout.job_title or "", allow_full=False)
    return from_title if from_title[0] != "UNKNOWN" else from_jd


def _holidays(scout: Scout) -> tuple[int | None, str | None]:
    for text in (scout.jd_text or "", scout.job_title or ""):
        matches = list(_HOLIDAYS.finditer(text))
        if matches:
            # If a JD gives multiple explicit annual totals, prefer the lower
            # bound instead of manufacturing a favorable guarantee.
            match = min(matches, key=lambda item: int(item.group(1)))
            return int(match.group(1)), _brief(match.group(0))
    return None, None


def _salary(scout: Scout) -> tuple[int | None, int | None, str | None]:
    text = scout.salary_text or ""
    for anchor in _ANNUAL_SALARY.finditer(text):
        window = text[anchor.start():anchor.start() + 95]
        salary_range = _MANYEN_RANGE.search(window)
        if salary_range:
            low, high = (int(salary_range.group(i).replace(",", "")) * 10_000 for i in (1, 2))
            if low <= high:
                return low, high, _brief(window[:salary_range.end()])
            return None, None, None
        single = _MANYEN_SINGLE.search(window)
        if single:
            amount = int(single.group(1).replace(",", "")) * 10_000
            qualifier = single.group(2)
            if qualifier == "以下":
                return None, amount, _brief(window[:single.end()])
            if qualifier in {"以上", "から", "～"}:
                return amount, None, _brief(window[:single.end()])
            return amount, amount, _brief(window[:single.end()])
    return None, None, None


def assess_priority(scout: Scout) -> PriorityAssessment | None:
    """Compute a tier only after Target Domain Gate has confirmed the role."""
    if assess_target_domain(scout).status != "TARGET_CONFIRMED":
        return None
    remote_level, remote_evidence = _remote(scout)
    annual_holidays, holidays_evidence = _holidays(scout)
    salary_min, salary_max, salary_evidence = _salary(scout)
    if remote_level == "FULL_REMOTE":
        tier: PriorityTier = "S"
        evidence = f"JD 明确：{remote_evidence}。"
    elif annual_holidays is not None and annual_holidays >= 125:
        tier = "A"
        evidence = f"明确年休：{holidays_evidence}。"
    elif remote_level in {"REMOTE_4_PLUS", "REMOTE_3"}:
        tier = "B"
        evidence = f"明确 Remote 频率：{remote_evidence}。"
    else:
        tier = "C"
        evidence = "未见 Full Remote、年休至少 125 日或每周至少 3 日 Remote 的明确证据。"
    return PriorityAssessment(
        priority_tier=tier, remote_level=remote_level, annual_holidays=annual_holidays,
        salary_min=salary_min, salary_max=salary_max, priority_evidence=evidence,
        salary_below_target=salary_max is not None and salary_max < 4_500_000,
        remote_evidence=remote_evidence, holidays_evidence=holidays_evidence,
        salary_evidence=salary_evidence,
    )
