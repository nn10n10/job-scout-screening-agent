"""Read-only adapter for type.jp's observed Scout / Offer DM pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError, Page

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from .base import PlatformAdapter


TYPE_ORIGIN = "https://type.jp"
DETAIL_PATHS = {
    "/scout/scout_detail/": ("scout", "scoutUserId"),
    "/scout/offerdm_detail/": ("offer_dm", "offerdmUserId"),
}
JOB_PATH = re.compile(r"/job-\d+/(?P<job_id>\d+)_detail/")
DATE_LABEL = re.compile(r"^(\d{1,2})/(\d{1,2})\(([月火水木金土日])\)$")
WEEKDAYS = {name: index for index, name in enumerate("月火水木金土日")}

TitleDecision = Literal["TITLE_SKIP", "TITLE_REVIEW", "DETAIL"]
CORE_ROLE = re.compile(
    r"cloud|aws|infra|infrastructure|\bsre\b|devops|platform|network|server|"
    r"クラウド|インフラ|基盤|ネットワーク|サーバ",
    re.IGNORECASE,
)
EXPLICIT_TARGET_ROLE = re.compile(
    r"(?:cloud|aws|infra|infrastructure|devops|platform|network|server)[\s/・-]*"
    r"(?:engineer|architect)|\bsre\b|"
    r"(?:クラウド|インフラ|基盤|ネットワーク|サーバ|DevOps)[^｜／/]{0,12}"
    r"(?:エンジニア|構築|運用設計)",
    re.IGNORECASE,
)
NON_TARGET_ROLE = re.compile(
    r"営業|買取|施工管理|事務|ドライバー|ビル[・\s]?マンション.{0,8}管理|"
    r"設備管理|マンションフロント|SNS運用|Webデザイナー|動画編集|人材管理|"
    r"販売スタッフ|店舗スタッフ|調理師|美容師|保育士|看護師|介護士|"
    r"フロントエンドエンジニア|Webクリエイター|プロジェクトマネージャー|"
    r"プロダクトマネージャー|プリセールス|\bPMO\b|\bPM\b|\bPL\b|"
    r"送迎|ハイヤー運転手|プランナー|管理サポート",
    re.IGNORECASE,
)
NEARBY_ROLE = re.compile(
    r"backend|back-end|バックエンド|社内SE|consult|コンサル|security|"
    r"セキュリティ|開発エンジニア|自社開発|Webエンジニア|AIエンジニア|"
    r"テクニカルサポート",
    re.IGNORECASE,
)
GENERIC_IT_ROLE = re.compile(r"ITエンジニア|システムエンジニア|\bSE\b|\bPG\b", re.IGNORECASE)
SES_SIGNALS = (
    (re.compile(r"還元率", re.IGNORECASE), "還元率"),
    (re.compile(r"案件選択制|案件は自分で選べる|希望案件", re.IGNORECASE), "案件選択"),
    (re.compile(r"単価連動", re.IGNORECASE), "単価連動"),
    (re.compile(r"会社都合のアサインなし", re.IGNORECASE), "会社都合のアサインなし"),
    (re.compile(r"常時[\d,]+万件", re.IGNORECASE), "大量案件"),
    (re.compile(r"営業が案件紹介", re.IGNORECASE), "営業による案件紹介"),
)
INHOUSE_CLOUD = re.compile(
    r"(?:自社(?:サービス|製品|プロダクト)|内製).{0,35}"
    r"(?:cloud|aws|infra|sre|devops|platform|クラウド|インフラ|基盤)|"
    r"(?:cloud|aws|infra|sre|devops|platform|クラウド|インフラ|基盤).{0,35}"
    r"(?:自社(?:サービス|製品|プロダクト)|内製)",
    re.IGNORECASE,
)
CLIENT_SITE_EVIDENCE = re.compile(r"客先常駐|顧客先常駐|クライアント先常駐")
SES_JOB_EVIDENCE = re.compile(r"\bSES(?:案件|として|エンジニア)\b|SES案件|SESとして", re.IGNORECASE)
SES_NEGATION = re.compile(
    r"SES(?:ではない|ではありません|なし|を行わない)|"
    r"(?:客先|顧客先|クライアント先)常駐(?:なし|はありません|しない|ではない|ゼロ)"
)
TYPE_TARGET_DUTY = re.compile(
    r"(?:クラウド|インフラ|IT基盤|AWS|Azure|ネットワーク|NW|サーバー?(?!サイド)|"
    r"Cloud|Infrastructure|Platform|SRE|DevOps|Server(?![- ]?side)|Network)"
    r".{0,45}(?:設計|構築|運用|移行|保守|改善)|"
    r"(?:設計|構築|運用|移行).{0,25}(?:クラウド|インフラ|AWS|Azure|ネットワーク|サーバー?)",
    re.IGNORECASE | re.DOTALL,
)
TYPE_TARGET_TITLE = re.compile(
    r"(?:クラウド|インフラ|IT基盤|AWS|Azure|ネットワーク|サーバー?(?!サイド)|"
    r"Cloud|Infrastructure|Platform|SRE|DevOps|Server(?![- ]?side)|Network)",
    re.IGNORECASE,
)
TYPE_NON_TARGET_MAIN = (
    (re.compile(r"Web(?:系|アプリ|システム).{0,35}開発|Webアプリケーション|"
                r"スマートフォンアプリ.{0,30}開発|業務系システム.{0,25}開発|"
                r"システム(?:の)?(?:設計・)?開発|新サービス開発", re.DOTALL), "Application/Web/System 开发"),
    (re.compile(r"サーバーサイド.{0,20}開発|バックエンド.{0,20}開発|フロントエンド.{0,20}開発", re.DOTALL),
     "Backend/Frontend 开发"),
    (re.compile(r"自動車関連.{0,30}組み込み開発|組込.{0,25}開発|車載.{0,25}(?:開発|テスト)"),
     "組込/车载开发"),
    (re.compile(r"ITサポート事務|ヘルプデスク.{0,20}担当|"
                r"CADオペレーター|テクニカルサポート業務|"
                r"評価、テスト作成|先輩エンジニアの補助"), "Helpdesk/IT 支援/测试"),
)
TYPE_CLIENT_ASSIGNMENT = re.compile(
    r"(?:客先|顧客先)常駐(?!なし|はありません|しない|ではない|ゼロ)|"
    r"クライアント先(?:にて|で|または|への出社)|"
    r"クライアント企業先|お客様先|客先案件|プロジェクト先|"
    r"派遣先|就業先|クライアント案件にアサイン",
)
TYPE_STRONG_SES = (
    (re.compile(r"還元率\s*\d|単価連動"), "還元率/単価連動"),
    (re.compile(r"案件.{0,8}選択(?:制)?|案件は.{0,15}選(?:ぶ|べる|択)|"
                r"案件を.{0,15}選(?:ぶ|べる|択)|案件選択はエンジニアが主体"), "案件選択"),
    (re.compile(r"会社都合.{0,15}アサイン|無理なアサイン|"
                r"営業が.{0,25}(?:案件|現場)?を?探|(?:案件|現場).{0,25}営業が探|"
                r"営業.{0,70}(?:案件|現場).{0,35}(?:探|紹介|提案)|"
                r"営業.{0,30}(?:探|紹介|提案).{0,35}(?:案件|現場)"), "営業紹介/会社都合アサイン"),
    (re.compile(r"(?:常時)?[\d,]+件(?:超|以上)?の案件|常時[\d,]+件(?:超|以上)?の案件|"
                r"常時[\d,]+件超の案件"), "大量案件からの配属"),
)
TYPE_GENERIC_PROJECT_PLACEMENT = re.compile(r"プロジェクト(?:先)?へ配属|案件へ配属|配属先を選")
TYPE_POOL_ASSIGNMENT = re.compile(
    r"(?:常時)?[\d,]+件(?:超|以上)?の案件から.{0,120}(?:紹介|提案|選|参画|配属)|"
    r"案件(?:プール|一覧).{0,80}(?:紹介|提案|配属)|"
    r"営業.{0,100}案件(?:を|から).{0,60}(?:紹介|提案|探)",
    re.DOTALL,
)
TYPE_COMMERCIAL_SES = (
    (TYPE_STRONG_SES[1][0], "案件選択"),
    (re.compile(r"単価連動"), "単価連動"),
    (re.compile(r"還元率\s*\d"), "還元率"),
    (re.compile(r"会社都合.{0,15}アサイン|無理なアサイン"), "会社都合アサイン"),
    (re.compile(r"営業.{0,100}(?:案件|現場).{0,60}(?:紹介|提案|探)|"
                r"(?:案件|現場).{0,40}営業が探", re.DOTALL), "営業による案件紹介"),
    (re.compile(r"待機(?:時|中|期間).{0,12}(?:給与|月給).{0,8}保証|"
                r"待機(?:時|中|期間).{0,12}給与あり"), "待機時給与保証"),
)
TYPE_INHOUSE_NO_CLIENT = re.compile(r"自社内勤務|基本自社勤務|常駐なし|客先常駐なし")


@dataclass(frozen=True)
class TypeScoutRef:
    external_id: str
    url: str
    scout_kind: str
    received_on: date | None
    company_name: str | None
    job_title: str | None
    scout_title: str | None
    job_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class TitlePrefilterResult:
    decision: TitleDecision
    reason: str
    explicit_target: bool = False


def prefilter_title(title: str | None) -> TitlePrefilterResult:
    """Conservative local title decision; never infer job duties from keywords alone."""
    if not title or not title.strip():
        return TitlePrefilterResult("DETAIL", "标题缺失，不能安全排除。")
    target_signal = CORE_ROLE.search(title)
    signals = [name for pattern, name in SES_SIGNALS if pattern.search(title)]
    # Target-role signals require the JD to resolve SES risk. Never reject a
    # Cloud/Infra/SRE/etc. title merely because it advertises project economics.
    if len(signals) >= 2 and not target_signal and not INHOUSE_CLOUD.search(title):
        return TitlePrefilterResult(
            "TITLE_SKIP", f"标题同时出现多项案件型信号（{'、'.join(signals)}），且无明确自社 Cloud/Infra 主职责。"
        )
    # A named core role wins over incidental words such as 営業が案件紹介.
    if EXPLICIT_TARGET_ROLE.search(title):
        return TitlePrefilterResult("DETAIL", "标题明确提及 Cloud/Infra/SRE/DevOps/Platform/Server/Network 方向。", True)
    if NON_TARGET_ROLE.search(title):
        return TitlePrefilterResult("TITLE_SKIP", "标题明确指向非目标职种或非 Cloud/Infra 主职责。")
    if target_signal:
        return TitlePrefilterResult("DETAIL", "标题提及 Cloud/Infra/SRE/DevOps/Platform/Server/Network，需查看实际职责。", True)
    if NEARBY_ROLE.search(title):
        return TitlePrefilterResult("TITLE_REVIEW", "相邻 IT 职种；仅凭标题无法确认 Cloud/Infra 是否为主要职责。")
    if GENERIC_IT_ROLE.search(title):
        return TitlePrefilterResult("DETAIL", "泛化 IT 标题，不能仅凭标题排除。")
    return TitlePrefilterResult("DETAIL", "标题信息不足，保守进入详情。")


def prefilter_jobs(titles: tuple[str, ...]) -> list[TitlePrefilterResult]:
    values = titles or ("",)
    decisions = [prefilter_title(title) for title in values]
    if any(item.explicit_target and item.decision == "DETAIL" for item in decisions):
        decisions = [
            TitlePrefilterResult(
                "TITLE_SKIP", "同一 offer 已有明确 Cloud/Infra 核心岗位；该泛化或相邻岗位暂不读取 JD。"
            ) if item.decision != "TITLE_SKIP" and not item.explicit_target else item
            for item in decisions
        ]
    return decisions


def title_is_clear_non_target(titles: tuple[str, ...]) -> bool:
    """Compatibility helper: an offer is skipped only when all jobs are skipped."""
    return bool(titles) and all(item.decision == "TITLE_SKIP" for item in prefilter_jobs(titles))


def _type_primary_jd(jd: str) -> str:
    return re.split(r"(?:^|\n)(?:案件例|開発環境・業務範囲)", jd, maxsplit=1)[0][:1800]


def type_primary_target_duty(scout: Scout) -> bool:
    """A target title plus primary-duty evidence, not an incidental tool/example."""
    title = scout.job_title or ""
    if re.search(r"サーバーサイド|server[- ]?side", title, re.IGNORECASE):
        return False
    return bool(TYPE_TARGET_TITLE.search(title) and TYPE_TARGET_DUTY.search(_type_primary_jd(scout.jd_text or "")))


def type_detail_local_skip_reason(scout: Scout) -> str | None:
    """Exclude evidenced non-target primary work before evaluating SES risk."""
    jd = scout.jd_text or ""
    primary = _type_primary_jd(jd)
    title = scout.job_title or ""
    if not primary:
        return None
    if re.search(r"初級(?:IT)?エンジニア", title) and re.search(r"研修", primary) \
            and re.search(r"監視|ヘルプデスク|テクニカルサポート", jd):
        return "JD 说明先培训再分配监视或 Helpdesk 等初级项目，Cloud/Infrastructure 并非当前主职。"
    if type_primary_target_duty(scout):
        return None
    # A generic title with both application and infrastructure duties does not
    # establish the actual assignment; leave it for the separate SES hard rule.
    if TYPE_TARGET_DUTY.search(primary) and re.search(r"ITエンジニア|\bSE\b", title, re.IGNORECASE):
        return None
    if re.search(r"ITサポート事務|CADオペレーター|テクニカルサポート業務|"
                 r"評価、テスト作成|先輩エンジニアの補助", primary):
        return "JD 主职责明确为 Helpdesk/IT 支援、测试或 CAD，非 Cloud/Infrastructure 主职。"
    for pattern, category in TYPE_NON_TARGET_MAIN:
        if pattern.search(primary):
            return f"JD 主职责明确为{category}，Cloud/Infrastructure 不是主职。"
    if re.search(r"開発エンジニア|Webエンジニア|サーバーサイドエンジニア", title) \
            and re.search(r"(?:Web|アプリ|業務システム|基幹システム).{0,30}開発", jd, re.DOTALL) \
            and not TYPE_TARGET_DUTY.search(primary):
        return "职位与 JD 项目例明确以 Application/Web/System 开发为主，非 Cloud/Infrastructure 主职。"
    if re.search(r"AI案件|AIプロジェクト", primary) and re.search(
        r"フロントエンド開発|AIエージェント|機械学習モデル|生成AI", jd,
    ) and not TYPE_TARGET_DUTY.search(primary):
        return "JD 主要介绍 AI/Application 开发项目，Cloud/Infrastructure 仅为零散项目例。"
    return None


def type_ses_hard_rule(scout: Scout) -> Evaluation | None:
    """Apply the user's non-SES/client-site red line only on combined evidence."""
    jd = scout.jd_text or ""
    if not jd:
        return None
    primary = _type_primary_jd(jd)
    location = scout.location_text or ""
    combined = " ".join((scout.job_title or "", jd, scout.salary_text or "", location))
    site = TYPE_CLIENT_ASSIGNMENT.search(location) or TYPE_CLIENT_ASSIGNMENT.search(primary)
    signals = [name for pattern, name in TYPE_STRONG_SES if pattern.search(combined)]
    pool = TYPE_POOL_ASSIGNMENT.search(jd)
    commercial = [name for pattern, name in TYPE_COMMERCIAL_SES if pattern.search(combined)]
    pool_without_client = bool(
        not site and pool and len(commercial) >= 2
        and not TYPE_INHOUSE_NO_CLIENT.search(" ".join((scout.job_title or "", primary, location)))
    )
    project_placement = TYPE_GENERIC_PROJECT_PLACEMENT.search(primary)
    target_main = type_primary_target_duty(scout)
    title = scout.job_title or ""
    target_title = bool(TYPE_TARGET_TITLE.search(title) and not re.search(
        r"サーバーサイド|server[- ]?side", title, re.IGNORECASE,
    ))
    if target_main or target_title:
        if not ((site and signals) or pool_without_client):
            return None
    elif not ((site and signals) or project_placement or pool_without_client):
        return None
    if not target_main and project_placement:
        signals.append("项目配属")
    if pool_without_client:
        signals = ["案件池介绍配属", *commercial]
    if not signals:
        return None
    return Evaluation(
        verdict="SKIP", confidence=0.9,
        summary="JD 的客先/项目配属与强案件制证据触发非 SES/非客先常驻硬红线。",
        reasons=[f"本地证据：{'客先/项目现场配属、' if site else ''}"
                 f"{'、'.join(dict.fromkeys(signals))}。"],
        concerns=["未将受託、直請け、チーム参画或普通的『案件』单独视为 SES。"],
        client_site=True if site else None,
    )


def type_post_detail_hard_rule(scout: Scout) -> Evaluation | None:
    """Apply only directly evidenced client-site/SES conflicts after reading JD."""
    jd = scout.jd_text or ""
    if not jd or SES_NEGATION.search(jd):
        return None
    client_site = bool(CLIENT_SITE_EVIDENCE.search(jd))
    ses_job = bool(SES_JOB_EVIDENCE.search(jd))
    if not client_site and not ses_job:
        return None
    evidence = "客先常駐" if client_site else "SES案件"
    return Evaluation(
        verdict="SKIP", confidence=0.85,
        summary="JD 明确显示与目标工作方式冲突的驻场或 SES 岗位。",
        reasons=[], concerns=[f"JD 明确提及 {evidence}，需人工核实具体工作形态。"],
        ses=True if ses_job else None, client_site=True if client_site else None,
    )


def parse_type_day(label: str, *, today: date) -> date | None:
    """Resolve type.jp's yearless MM/DD(weekday) to the latest valid past date."""
    match = DATE_LABEL.fullmatch(label.strip())
    if match is None:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    weekday = WEEKDAYS[match.group(3)]
    for year in (today.year, today.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today and candidate.weekday() == weekday:
            return candidate
    return None


def parse_type_ref(
    href: str, date_label: str, *, today: date, company_name: str | None = None,
    job_titles: list[str] | None = None, scout_title: str | None = None,
) -> TypeScoutRef:
    parsed = urlsplit(urljoin(TYPE_ORIGIN, href))
    if parsed.scheme != "https" or parsed.netloc != "type.jp" or parsed.path not in DETAIL_PATHS:
        raise ValueError("Unexpected type Scout detail link")
    kind, user_key = DETAIL_PATHS[parsed.path]
    query = parse_qs(parsed.query)
    mid = query.get("mid", [])
    user_id = query.get(user_key, [])
    if len(mid) != 1 or not mid[0].isdigit() or len(user_id) != 1 or not user_id[0].isdigit():
        raise ValueError("type Scout detail link has no stable numeric message ID")
    titles = [title.strip() for title in (job_titles or []) if title.strip()]
    return TypeScoutRef(
        external_id=f"{kind}:{mid[0]}",
        url=urljoin(TYPE_ORIGIN, href),
        scout_kind=kind,
        received_on=parse_type_day(date_label, today=today),
        company_name=company_name.strip() or None if company_name else None,
        job_title=titles[0] if len(titles) == 1 else None,
        scout_title=scout_title.strip() or None if scout_title else None,
        job_titles=tuple(titles),
    )


def _text(locator) -> str | None:
    return locator.first.inner_text().strip() or None if locator.count() else None


class TypeJpAdapter(PlatformAdapter):
    platform_name = "type"
    scout_list_url = f"{TYPE_ORIGIN}/scout/"

    def is_logged_in(self, page: Page | None) -> bool:
        return (
            page is not None
            and urlsplit(page.url).netloc == "type.jp"
            and urlsplit(page.url).path == "/scout/"
            and page.locator("section.main-list").count() > 0
        )

    def get_scout_list(self, page: Page | None, *, limit: int = 100) -> list[TypeScoutRef]:
        if page is None:
            raise ValueError("type requires a browser page")
        links = page.locator(
            'a[href*="/scout/scout_detail/"],a[href*="/scout/offerdm_detail/"]'
        )
        refs: list[TypeScoutRef] = []
        today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
        for link in links.all()[:limit]:
            href = link.get_attribute("href")
            if href is None:
                continue
            card = link.locator("xpath=ancestor::section[1]")
            if card.count() != 1:
                continue
            date_label = _text(card.locator("xpath=ancestor::div[contains(@class,'date-unit')][1]/p[1]")) or ""
            try:
                refs.append(parse_type_ref(
                    href, date_label, today=today,
                    company_name=_text(card.locator("p.company-name")),
                    job_titles=card.locator("p.job-name").all_inner_texts(),
                    scout_title=_text(card.locator("h2")),
                ))
            except ValueError:
                continue
        return refs

    def get_scout_detail(
        self, page: Page | None, ref: TypeScoutRef, *, include_indices: set[int] | None = None,
    ) -> list[dict]:
        if page is None:
            raise ValueError("type requires a browser page")
        detail = page.context.new_page()
        try:
            detail.goto(ref.url, wait_until="domcontentloaded", timeout=15000)
            parsed = urlsplit(detail.url)
            if parsed.netloc != "type.jp" or parsed.path not in DETAIL_PATHS:
                raise ValueError("type detail redirected away from a Scout page")
            query = parse_qs(parsed.query)
            if f"{DETAIL_PATHS[parsed.path][0]}:{query.get('mid', [''])[0]}" != ref.external_id:
                raise ValueError("type detail message ID does not match list ID")
            summary = detail.locator("main section.summary").first
            message = detail.locator("main section.message").first
            titles = [value.strip() for value in summary.locator("p.job-name").all_inner_texts() if value.strip()]
            company_name = _text(summary.locator("p.company-name")) or ref.company_name
            scout_text = _text(message.locator("p.main-text"))
            job_links = message.locator("a[href]").evaluate_all(
                "nodes => nodes.map(node => ({href: node.getAttribute('href'), title: node.innerText.trim()}))"
            )
            linked_jobs: list[tuple[str, str]] = []
            for link in job_links:
                href = link.get("href")
                if not href:
                    continue
                url = urljoin(TYPE_ORIGIN, href)
                parts = urlsplit(url)
                if parts.netloc == "type.jp" and JOB_PATH.fullmatch(parts.path) and url not in [item[0] for item in linked_jobs]:
                    linked_jobs.append((url, link.get("title", "").strip()))
            base = {
                "platform": self.platform_name, "company_name": company_name,
                "scout_title": ref.scout_title, "scout_text": scout_text,
                "url": ref.url, "received_on": ref.received_on,
                "received_at": None, "scout_kind": ref.scout_kind,
                "sender_kind": None, "is_bulk_like": None,
            }
            if not linked_jobs:
                return [
                    {**base, "id": ref.external_id if len(ref.job_titles) <= 1
                     else f"{ref.external_id}:list:{index + 1}",
                     "job_title": title or None, "jd_text": None, "_list_index": index}
                    for index, title in enumerate(ref.job_titles or tuple(titles) or ("",))
                    if include_indices is None or index in include_indices
                ]
            records = []
            used_indices: set[int] = set()
            for job_url, link_title in linked_jobs:
                match = JOB_PATH.fullmatch(urlsplit(job_url).path)
                assert match is not None
                list_index = next(
                    (index for index, title in enumerate(ref.job_titles)
                     if index not in used_indices and " ".join(title.split()) == " ".join(link_title.split())),
                    None,
                )
                if list_index is not None:
                    used_indices.add(list_index)
                if include_indices is not None and list_index is not None and list_index not in include_indices:
                    continue
                job = self._read_job(page, job_url)
                if job is None:
                    raise RuntimeError("type job detail unavailable; offer left pending for retry")
                records.append({
                    **base, "id": f"{ref.external_id}:job:{match.group('job_id')}",
                    "job_title": job["title"], "jd_text": job["jd_text"],
                    "salary_text": job["salary_text"],
                    "location_text": job["location_text"],
                    "_list_index": list_index,
                })
            return records
        finally:
            detail.close()

    @staticmethod
    def _read_job(page: Page, url: str) -> dict | None:
        job_page = page.context.new_page()
        try:
            job_page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if urlsplit(job_page.url).netloc != "type.jp" or not JOB_PATH.fullmatch(urlsplit(job_page.url).path):
                return None
            sections = (
                "section.uq-detail-outline", "section.uq-detail-project", "section.uq-detail-environ",
                "section.uq-detail-free_point1", "section.uq-detail-required", "section.uq-detail-workstyle",
            )
            jd_parts = [_text(job_page.locator(selector)) for selector in sections]
            return {
                "title": _text(job_page.locator("h1.jobname")),
                "jd_text": "\n".join(part for part in jd_parts if part) or None,
                "salary_text": _text(job_page.locator("section.uq-detail-salary")),
                "location_text": _text(job_page.locator("section.uq-detail-location")),
            }
        except PlaywrightError:
            return None
        finally:
            job_page.close()

    def normalize_scout(self, raw: dict) -> Scout:
        return Scout.model_validate(raw)
