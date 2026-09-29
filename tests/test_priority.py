"""Fictional evidence tests; no model, browser, or real Scout data."""

import pytest

from scout_agent.models.scout import Scout
from scout_agent.priority import PriorityAssessment, assess_priority


def _cloud(jd: str, *, title: str = "Cloud Engineer", salary: str | None = None) -> Scout:
    return Scout(
        platform="type", job_title=title,
        jd_text="AWS 基盤の設計・構築を主担当。\n" + jd,
        salary_text=salary,
    )


@pytest.mark.parametrize("jd,tier,remote,holidays", [
    ("原則フルリモート勤務。年間休日130日。", "S", "FULL_REMOTE", 130),
    ("完全在宅勤務。", "S", "FULL_REMOTE", None),
    ("年間休日125日。週2日リモート可。", "A", "REMOTE_1_2", 125),
    ("年間休日124日。週3日リモート可。", "B", "REMOTE_3", 124),
    ("週4日以上リモート勤務。", "B", "REMOTE_4_PLUS", None),
    ("週3日以上リモート勤務。", "B", "REMOTE_3", None),
    ("週3～4日リモート勤務。", "B", "REMOTE_3", None),
    ("週2～3日リモート勤務。", "C", "REMOTE_RANGE", None),
    ("週2日以上リモート勤務。", "C", "REMOTE_RANGE", None),
    ("週3日程度リモート勤務。", "C", "REMOTE_RANGE", None),
    ("週2日リモート可。", "C", "REMOTE_1_2", None),
    ("リモート可。", "C", "REMOTE_UNKNOWN", None),
    ("月2回リモート可。", "C", "REMOTE_OCCASIONAL", None),
    ("原則出社。", "C", "ONSITE", None),
    ("勤務方法は入社後に決定。", "C", "UNKNOWN", None),
    ("フルリモート可・相談可能。", "C", "REMOTE_UNKNOWN", None),
    ("フルリモートOK。", "C", "REMOTE_UNKNOWN", None),
    ("フルリモートでの勤務も相談可能です。", "C", "REMOTE_UNKNOWN", None),
    ("フルリモート案件も紹介します。", "C", "REMOTE_UNKNOWN", None),
])
def test_priority_tier_uses_evidence_and_precedence(jd, tier, remote, holidays):
    result = assess_priority(_cloud(jd))
    assert result is not None
    assert (result.priority_tier, result.remote_level, result.annual_holidays) == (
        tier, remote, holidays,
    )
    assert result.priority_evidence


def test_full_remote_in_title_alone_is_not_tier_s():
    result = assess_priority(_cloud("勤務方法は要相談。", title="フルリモート可 Cloud Engineer"))
    assert result is not None
    assert (result.priority_tier, result.remote_level) == ("C", "REMOTE_UNKNOWN")


@pytest.mark.parametrize("salary,minimum,maximum,below", [
    ("初年度の年収\n567万円～783万円", 5_670_000, 7_830_000, False),
    ("想定年収600万円～1200万円。月給44万円～85万円", 6_000_000, 12_000_000, False),
    ("年収400万円～600万円", 4_000_000, 6_000_000, False),
    ("年収350万円～430万円", 3_500_000, 4_300_000, True),
    ("年収430万円以下", None, 4_300_000, True),
    ("年収430万円", 4_300_000, 4_300_000, True),
    ("月給35万円＋賞与", None, None, False),
    (None, None, None, False),
])
def test_priority_salary_only_uses_explicit_annual_bounds(salary, minimum, maximum, below):
    result = assess_priority(_cloud("週2日リモート勤務。", salary=salary))
    assert result is not None
    assert (result.salary_min, result.salary_max, result.salary_below_target) == (
        minimum, maximum, below,
    )
    assert PriorityAssessment.from_json(result.to_json()) == result


def test_non_confirmed_target_has_no_priority_tier():
    scout = Scout(platform="type", job_title="ITエンジニア",
                  jd_text="クラウド案件約半数。配属は希望と経験で決定。")
    assert assess_priority(scout) is None
