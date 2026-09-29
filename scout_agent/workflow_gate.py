"""Read-only, evidence-based gate for mandatory pre-selection workflow steps.

This gate is independent of target domain, priority, and stored evaluations.
Only an explicitly mandatory casual interview rejects a Scout; silence stays
unknown and optional invitations remain acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from scout_agent.models.scout import Scout


WorkflowStatus = Literal["WORKFLOW_OK", "WORKFLOW_UNKNOWN", "WORKFLOW_REJECTED"]


@dataclass(frozen=True)
class WorkflowAssessment:
    status: WorkflowStatus
    evidence: str
    reason: str


_CASUAL = re.compile(r"カジュアル\s*面談", re.I)
_DIRECT_MUST = re.compile(
    r"(?:カジュアル\s*面談(?:への)?(?:参加)?(?:は|が|を)?\s*"
    r"(?:必須(?!ではない|でない|ではありません)|必ず(?:参加|実施|受け)|参加が必要)|"
    r"(?:必須|必ず)\s*カジュアル\s*面談)",
    re.I,
)
_MANDATORY = re.compile(
    r"(?:カジュアル\s*面談.{0,16}(?:必須|必ず|参加が必要|参加必須|必ず参加|必ず実施|"
    r"必ず受け)|"
    r"(?:必須|必ず|必ず参加|必ず実施).{0,16}カジュアル\s*面談|"
    r"(?:まず(?:は)?|最初に|はじめに|選考前に|正式選考前に|書類選考前に)"
    r".{0,12}カジュアル\s*面談|"
    r"カジュアル\s*面談.{0,12}(?:から(?:スタート|始ま)|を経て(?:選考|面接)))",
    re.I,
)
_OPTIONAL = re.compile(
    r"(?:希望者|ご希望|希望に応じ|任意|歓迎).{0,24}カジュアル\s*面談|"
    r"カジュアル\s*面談.{0,16}(?:歓迎|任意|も可能|可能|も可|できます|選択可|"
    r"希望者のみ|ご希望|希望に応じ|不要|必須ではない|必須ではありません)",
    re.I,
)
_SELECTION_HEADING = re.compile(r"選考(?:プロセス|フロー|の流れ|手順|ステップ)", re.I)
_FLOW_AFTER_CASUAL = re.compile(
    r"^\s*(?:[■●・\-]\s*)?カジュアル\s*面談\s*(?:⇒|→|➜|＞|>)\s*"
    r"(?:適性|書類|面接|一次|二次|最終|正式選考|選考)",
    re.I,
)


def _brief(text: str) -> str:
    return " ".join(text.split())[:110]


def assess_workflow(scout: Scout) -> WorkflowAssessment:
    """Classify only explicit JD/Scout workflow language; never infer from LLM output."""
    optional_evidence = ""
    for source in (scout.jd_text or "", scout.scout_text or ""):
        lines = source.splitlines()
        for index, line in enumerate(lines):
            casual = _CASUAL.search(line)
            if not casual:
                continue
            # A clear optional qualifier in this line defeats ambiguous
            # sequence wording, but not an explicit mandatory requirement.
            explicit_must = bool(_DIRECT_MUST.search(line))
            optional = bool(_OPTIONAL.search(line))
            mandatory = bool(_MANDATORY.search(line))
            preceding = " ".join(lines[max(0, index - 3):index])[-160:]
            selection_flow = bool(
                (_SELECTION_HEADING.search(preceding)
                 or _SELECTION_HEADING.search(line[:casual.start()]))
                and _FLOW_AFTER_CASUAL.search(line[casual.start():])
            )
            if explicit_must or ((mandatory or selection_flow) and not optional):
                return WorkflowAssessment(
                    "WORKFLOW_REJECTED", _brief(line),
                    "JD／Scout 明确要求正式选考前先参加 Casual 面谈。",
                )
            if optional and not optional_evidence:
                optional_evidence = _brief(line)
    if optional_evidence:
        return WorkflowAssessment(
            "WORKFLOW_OK", optional_evidence, "Casual 面谈明确为可选或受欢迎，并非必经流程。",
        )
    return WorkflowAssessment("WORKFLOW_UNKNOWN", "", "未见足以确认 Casual 面谈是否必经的证据。")
