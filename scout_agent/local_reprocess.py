"""Run platform-specific local rules against already stored, complete Scouts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.platforms.doda import doda_detail_prefilter
from scout_agent.platforms.mynavi import mynavi_detail_prefilter, mynavi_primary_target_duty
from scout_agent.platforms.type_jp import prefilter_title, type_post_detail_hard_rule


@dataclass(frozen=True)
class LocalResult:
    decision: Literal["local_skip", "classifier_candidate"]
    reason: str
    evaluation: Evaluation | None = None
    model_name: str | None = None


def _doda_rules(scout: Scout) -> LocalResult:
    reason = doda_detail_prefilter(scout)
    if reason is not None:
        return LocalResult(
            "local_skip", reason,
            Evaluation(
                verdict="SKIP", confidence=0.9,
                summary="详情显示岗位主要职责与目标 Cloud/Infrastructure 方向不符。",
                reasons=[reason], concerns=[],
            ),
            "doda-detail-prefilter",
        )
    evaluation = type_post_detail_hard_rule(scout)
    if evaluation is not None:
        return LocalResult("local_skip", "JD 命中本地驻场或 SES hard rule。", evaluation, "doda-hard-rule")
    if prefilter_title(scout.job_title).decision == "TITLE_SKIP":
        return LocalResult(
            "local_skip", "详情职位标题明确不属于 IT 或 Cloud/Infrastructure 方向。",
            Evaluation(
                verdict="SKIP", confidence=0.9,
                summary="职位标题明确不属于 IT 或 Cloud/Infrastructure 方向。",
                reasons=[], concerns=["仅按明确的职位标题做本地排除。"],
            ),
            "doda-title-fallback",
        )
    return LocalResult("classifier_candidate", "本地规则未确认明确冲突；需要 classifier 判断。")


def _mynavi_rules(scout: Scout) -> LocalResult:
    reason = mynavi_detail_prefilter(scout)
    if reason is not None:
        return LocalResult(
            "local_skip", reason,
            Evaluation(
                verdict="SKIP", confidence=0.9,
                summary="详情显示岗位主要职责与目标 Cloud/Infrastructure 方向不符。",
                reasons=[reason], concerns=[],
            ),
            "mynavi-detail-prefilter",
        )
    evaluation = type_post_detail_hard_rule(scout)
    if evaluation is not None:
        return LocalResult("local_skip", "JD 命中本地驻场或 SES hard rule。", evaluation, "mynavi-hard-rule")
    if not mynavi_primary_target_duty(scout.jd_text) and prefilter_title(scout.job_title).decision == "TITLE_SKIP":
        return LocalResult(
            "local_skip", "详情职位标题明确不属于 IT 或 Cloud/Infrastructure 方向。",
            Evaluation(
                verdict="SKIP", confidence=0.9,
                summary="职位标题明确不属于 IT 或 Cloud/Infrastructure 方向。",
                reasons=[], concerns=["仅按明确的职位标题做本地排除。"],
            ),
            "mynavi-title-fallback",
        )
    return LocalResult("classifier_candidate", "本地规则未确认明确冲突；需要 classifier 判断。")


LOCAL_RULES: dict[str, Callable[[Scout], LocalResult]] = {
    "doda": _doda_rules,
    "mynavi": _mynavi_rules,
}


def reprocess_stored_scout(scout: Scout) -> LocalResult:
    """Return a local decision without browser or model access."""
    if not scout.jd_text or not scout.jd_text.strip():
        raise ValueError("Stored Scout has no complete JD")
    try:
        rules = LOCAL_RULES[scout.platform]
    except KeyError as exc:
        raise ValueError(f"No stored-scout local rules for {scout.platform}") from exc
    return rules(scout)
