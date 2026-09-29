"""Fictional, de-identified Casual interview workflow examples."""

import pytest

from scout_agent.models.scout import Scout
from scout_agent.priority import assess_priority
from scout_agent.target_domain import assess_target_domain
from scout_agent.workflow_gate import assess_workflow


@pytest.mark.parametrize("jd", [
    "選考の流れ：カジュアル面談必須。その後に正式面接。",
    "まずカジュアル面談にご参加ください。その後、書類選考です。",
    "正式選考前にカジュアル面談を行います。",
    "選考プロセス\n以下の順で進みます。\nカジュアル面談⇒適性検査⇒一次面接",
    "選考フロー: カジュアル面談→書類選考→面接",
])
def test_mandatory_casual_interview_is_rejected(jd):
    result = assess_workflow(Scout(platform="green", jd_text=jd))
    assert result.status == "WORKFLOW_REJECTED"
    assert "カジュアル面談" in result.evidence


@pytest.mark.parametrize("jd", [
    "カジュアル面談歓迎。応募は直接可能です。",
    "希望者はカジュアル面談可。",
    "カジュアル面談も可能です。",
    "選考前にカジュアル面談をご希望の場合はご相談ください。",
    "まずカジュアル面談も可能ですが、直接応募もできます。",
    "カジュアル面談は任意ですが、応募時には必ず履歴書をご提出ください。",
    "カジュアル面談は必須ではありません。直接選考に進めます。",
])
def test_optional_casual_interview_is_not_rejected(jd):
    assert assess_workflow(Scout(platform="type", jd_text=jd)).status == "WORKFLOW_OK"


def test_unknown_stays_unknown_and_scout_text_is_considered():
    assert assess_workflow(Scout(platform="green", jd_text="AWS 基盤を構築。")).status == "WORKFLOW_UNKNOWN"
    assert assess_workflow(Scout(platform="green", jd_text="カジュアル面談について応相談。")).status == "WORKFLOW_UNKNOWN"
    scout = Scout(platform="green", scout_text="カジュアル面談必須です。")
    assert assess_workflow(scout).status == "WORKFLOW_REJECTED"


def test_workflow_rejection_does_not_change_target_or_priority():
    scout = Scout(
        platform="green", job_title="Cloud Engineer",
        jd_text="AWS 基盤の設計・構築を主担当。年間休日130日。"
                "\n選考プロセス\nカジュアル面談⇒適性検査⇒面接",
    )
    assert assess_target_domain(scout).status == "TARGET_CONFIRMED"
    assert assess_priority(scout).priority_tier == "A"
    assert assess_workflow(scout).status == "WORKFLOW_REJECTED"
