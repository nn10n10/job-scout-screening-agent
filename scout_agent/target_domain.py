"""Conservative, read-only assessment of a Scout's *primary* target domain.

This is a narrower gate after the existing local hard rules, not a replacement
for them. It never changes a stored Scout or historical evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from scout_agent.models.scout import Scout


TargetStatus = Literal["TARGET_CONFIRMED", "TARGET_UNCERTAIN", "TARGET_REJECTED"]
TargetCategory = Literal["CLOUD", "PLATFORM", "SRE", "DEVOPS", "MODERN_INFRA", "NONE"]


@dataclass(frozen=True)
class TargetAssessment:
    status: TargetStatus
    category: TargetCategory
    evidence: str
    reason: str


_STRUCTURED_FIELD = re.compile(r"(?m)^([A-Za-z][A-Za-z0-9]*):\s*")
_CLOUD = re.compile(
    r"(?<![A-Za-z])(?:AWS|Azure(?!\s*(?:AD|Active Directory))|GCP|OCI|Cloud)(?![A-Za-z])|クラウド(?!時代)",
    re.I,
)
_CLOUD_INFRA = re.compile(
    r"(?:\b(?:AWS|Azure(?!\s*(?:AD|Active Directory))|GCP|OCI|Cloud)\b|クラウド(?!時代))[^\n。]{0,35}"
    r"(?:基盤|インフラ|環境の設計|環境構築|移行|Infrastructure|architecture|IaC|運用自動化|構築)|"
    r"(?:クラウド(?!時代)|Cloud|AWS|Azure|GCP|OCI)(?:基盤|環境|インフラ|移行|構築)|"
    r"(?:基盤|インフラ)[^\n。]{0,30}(?:\b(?:AWS|Azure(?!\s*(?:AD|Active Directory))|GCP|OCI|Cloud)\b|クラウド(?!時代))",
    re.I,
)
_INFRA_ACTION = re.compile(r"設計|構築|最適化|自動化|改善|移行|運用|提供|整備|管理|build|design|automation", re.I)
_PLATFORM_ROLE = re.compile(r"Platform Engineering|Platform Engineer|プラットフォームエンジニア|開発者基盤|Developer Platform", re.I)
_PLATFORM_WORK = re.compile(r"Kubernetes|EKS|コンテナ|CI/CD|セルフサービス|Namespace|開発基盤|observability|オブザーバビリティ", re.I)
_SRE_ROLE = re.compile(r"\bSRE\b|Site Reliability|信頼性エンジニア", re.I)
_SRE_WORK = re.compile(r"信頼性|可用性|SLO|SLI|observability|オブザーバビリティ|監視基盤|運用自動化|障害.*改善", re.I)
_DEVOPS_ROLE = re.compile(r"\bDevOps\b", re.I)
_DEVOPS_WORK = re.compile(r"CI/CD|デプロイ.*自動化|IaC|Terraform|Kubernetes|EKS|ECS|パイプライン", re.I)
_MODERN_INFRA = re.compile(
    r"(?:Terraform|\bIaC\b).{0,45}(?:基盤|インフラ|設計|構築|自動化|運用)|"
    r"(?:基盤|インフラ).{0,45}(?:Terraform|\bIaC\b)|"
    r"(?:Kubernetes|EKS|ECS|observability|オブザーバビリティ)"
    r".{0,45}(?:基盤|プラットフォーム|インフラ|設計|構築|最適化|提供)",
    re.I | re.S,
)
_MIXED_ASSIGNMENT = re.compile(
    r"(?:アプリ|開発|Web).{0,45}(?:インフラ|クラウド)|"
    r"(?:インフラ|クラウド).{0,45}(?:アプリ|開発系)|"
    r"(?:希望|適性|スキル|経験).{0,60}(?:配属|ポジション|案件)|"
    r"(?:配属|ポジション|案件).{0,60}(?:希望|適性|スキル|経験)|"
    r"案件の約半数|クラウド案件(?:あり|有)|クラウド案件約半数|"
    r"いずれかに配属|最適なポジション|"
    r"(?:経験|志向).{0,20}(?:経験|志向).{0,20}応じて|"
    r"(?:希望|条件).{0,25}(?:マッチング|紹介)",
    re.I | re.S,
)
_TRADITIONAL_MAIN = re.compile(
    r"Oracle\s*EBS|\bEBS\b|社内SE|情シス|ヘルプデスク|キッティング|"
    r"配信設備|デジタルサイネージ|LANスイッチ|ルータ|ファイアウォール|"
    r"サーバーリプレイス|サーバーのリプレイス|"
    r"Webアプリ(?:ケーション)?開発|アプリケーションの設計・開発",
    re.I,
)
_NON_TARGET_MAIN = re.compile(
    r"(?:Web|アプリ|バックエンド|フロントエンド|サーバーサイド|サービス|業務システム|"
    r"展示会管理システム).{0,28}(?:開発|実装)|"
    r"(?:開発|実装).{0,22}(?:エンジニア|をお任せ)|"
    r"(?:運用保守|監視業務).{0,35}(?:中心|メイン|お任せ)|"
    r"(?:M365|Microsoft\s*365|端末管理|クライアント端末|社内ネットワーク|"
    r"アカウント管理|ヘルプデスク|キッティング)",
    re.I | re.S,
)
_PROJECT_EXAMPLE_HEADING = re.compile(
    r"(?:^|\n)(?:【(?:具体的な)?プロジェクト事例】|【(?:具体的な)?プロジェクト例】|"
    r"[【《〈]案件例[】》〉]|プロジェクト事例|案件例)", re.I,
)
_ROUTINE_FIRST = re.compile(
    r"監視スクリプト|障害復旧テスト|アラート検知時の一次対応|"
    r"ヘルプデスク|キッティング|端末管理|アカウント管理",
    re.I,
)
def _structured_sections(jd: str) -> tuple[str, ...]:
    fields = list(_STRUCTURED_FIELD.finditer(jd))
    if not fields:
        return ()
    values = {
        match.group(1): jd[match.end():fields[index + 1].start() if index + 1 < len(fields) else len(jd)].strip()
        for index, match in enumerate(fields)
    }
    outline = values.get("jobContentOutline", "")
    detail = values.get("jobContentDetail", "")
    if not outline and not detail:
        return ()
    # Duties, not applicant requirements, examples, benefits, or technology lists.
    outline = _PROJECT_EXAMPLE_HEADING.split(outline, maxsplit=1)[0]
    detail = re.split(r"【雇(?:入|い)れ?入?直後】|【変更の範囲】|案件例|プロジェクト例", detail, maxsplit=1)[0]
    detail = _PROJECT_EXAMPLE_HEADING.split(detail, maxsplit=1)[0]
    return ((outline + "\n" + detail[:850]).strip(),)


def _project_example(jd: str) -> str:
    fields = list(_STRUCTURED_FIELD.finditer(jd))
    for index, match in enumerate(fields):
        if match.group(1) == "projectCase":
            return jd[match.end():fields[index + 1].start() if index + 1 < len(fields) else len(jd)]
    heading = _PROJECT_EXAMPLE_HEADING.search(jd)
    if heading:
        return jd[heading.end():]
    return ""


def _primary_sections(scout: Scout) -> tuple[str, ...]:
    jd = scout.jd_text or ""
    structured = _structured_sections(jd)
    if structured:
        return structured
    title_parts = (scout.job_title or "").split(" / ")
    starts = sorted({jd.find(part[:20]) for part in title_parts if len(part) >= 8 and jd.find(part[:20]) >= 0})
    if len(starts) > 1:
        sections = [jd[start:starts[index + 1] if index + 1 < len(starts) else len(jd)]
                    for index, start in enumerate(starts)]
    else:
        sections = [jd]
    primary = []
    for section in sections:
        # Green includes company profile and technology tags before the actual
        # job duties. Those are not evidence of what this hire will do.
        heading = re.search(r"(?m)^仕事内容\s*$", section)
        if heading:
            section = section[heading.end():]
        section = _PROJECT_EXAMPLE_HEADING.split(section, maxsplit=1)[0]
        section = re.split(r"(?:^|\n)(?:開発環境・業務範囲|応募資格|求める人物像)",
                           section, maxsplit=1)[0]
        primary.append(section[:1200])
    return tuple(primary)


def _brief_evidence(text: str, match: re.Match[str] | None) -> str:
    if match is None:
        return ""
    start = max(0, match.start() - 35)
    end = min(len(text), match.end() + 35)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:110]


def _has_duty_action(text: str, match: re.Match[str] | None) -> bool:
    if match is None:
        return False
    start = max(text.rfind("。", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
    boundaries = [pos for pos in (text.find("。", match.end()), text.find("\n", match.end())) if pos >= 0]
    end = min([*boundaries, match.end() + 90, len(text)])
    return bool(_INFRA_ACTION.search(text[start:end]))


def assess_target_domain(scout: Scout) -> TargetAssessment:
    """Require evidenced modern Cloud/Platform/SRE/DevOps as a main duty.

    A role title or tool name alone never confirms the domain. Ambiguous mixed
    assignments stay UNCERTAIN; distinct multi-job duty sections may be
    confirmed independently when one is explicitly a target role.
    """
    title = scout.job_title or ""
    sections = tuple(section for section in _primary_sections(scout) if section.strip())
    if not sections:
        return TargetAssessment("TARGET_UNCERTAIN", "NONE", "", "缺少可核实的 JD 主职责。")
    uncertain: TargetAssessment | None = None
    for section in sections:
        primary = section[:1050]
        role = title if len(sections) == 1 else section.splitlines()[0][:180]
        mixed = bool(_MIXED_ASSIGNMENT.search(primary))
        traditional = bool(_TRADITIONAL_MAIN.search((role + " " + primary[:280])))
        # A cloud tool in an application team's ancillary work, internal PC
        # estate, or future training is not a cloud-infrastructure main duty.
        non_target_main = bool(_NON_TARGET_MAIN.search(role + " " + primary[:240]))
        cloud = _CLOUD.search(primary)
        platform = _PLATFORM_ROLE.search(role + " " + primary[:350])
        sre = _SRE_ROLE.search(role + " " + primary[:350])
        devops = _DEVOPS_ROLE.search(role + " " + primary[:350])
        platform_work = _PLATFORM_WORK.search(primary)
        sre_work = _SRE_WORK.search(primary)
        devops_work = _DEVOPS_WORK.search(primary)
        cloud_work = _CLOUD_INFRA.search(primary)
        modern_work = _MODERN_INFRA.search(primary)

        category: TargetCategory = "NONE"
        evidence_match: re.Match[str] | None = None
        if platform and _has_duty_action(primary, platform_work):
            category, evidence_match = "PLATFORM", platform_work
        elif sre and _has_duty_action(primary, sre_work):
            category, evidence_match = "SRE", sre_work
        elif devops and _has_duty_action(primary, devops_work):
            category, evidence_match = "DEVOPS", devops_work
        elif _has_duty_action(primary, cloud_work):
            category, evidence_match = "CLOUD", cloud_work
        elif _has_duty_action(primary, modern_work):
            category, evidence_match = "MODERN_INFRA", modern_work

        if non_target_main and not (
            category in {"PLATFORM", "SRE", "DEVOPS"}
            and (platform or sre or devops)
        ):
            continue

        # A cloud bullet after routine monitoring/support duties in an
        # undifferentiated infra task menu does not establish a cloud main job.
        if (category in {"CLOUD", "MODERN_INFRA"} and evidence_match
                and _ROUTINE_FIRST.search(primary[:evidence_match.start()])
                and not _CLOUD.search(role)):
            mixed = True

        # A listed option within a mixed recruitment pool, an application role,
        # or a traditional EBS/facilities role is not a confirmed assignment.
        specific_role = category in {"PLATFORM", "SRE", "DEVOPS"} and bool(platform or sre or devops)
        if category != "NONE" and not traditional and (not mixed or specific_role):
            return TargetAssessment(
                "TARGET_CONFIRMED", category, _brief_evidence(primary, evidence_match),
                "JD 主职责明确包含现代云基础设施、平台、SRE 或 DevOps 工作。",
            )
        potential = cloud or platform or sre or devops or modern_work
        if potential:
            possible_category: TargetCategory = category if category != "NONE" else (
                "PLATFORM" if platform else "SRE" if sre else "DEVOPS" if devops else
                "CLOUD" if cloud else "MODERN_INFRA"
            )
            uncertain = TargetAssessment(
                "TARGET_UNCERTAIN", possible_category,
                _brief_evidence(primary, evidence_match or cloud or platform or sre or devops or modern_work),
                "提及目标方向，但实际主职责或配属未获明确证实。",
            )
    if uncertain is not None:
        return uncertain
    # A cloud construction *example* is relevant, but cannot establish the
    # actual assignment or main duty. Applicant skill requirements never count.
    project = _project_example(scout.jd_text or "")
    example_devops = _DEVOPS_ROLE.search(project)
    example = example_devops or _CLOUD.search(project)
    if example and _INFRA_ACTION.search(project):
        return TargetAssessment(
            "TARGET_UNCERTAIN", "DEVOPS" if example_devops else "CLOUD",
            _brief_evidence(project, example),
            "目标方向仅出现在项目例中，实际主职责或配属未确认。",
        )
    return TargetAssessment("TARGET_REJECTED", "NONE", "", "现有 JD 主职责未显示现代云基础设施方向。")
