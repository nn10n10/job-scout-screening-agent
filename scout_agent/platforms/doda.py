"""Read-only doda corporate-offer adapter, based on the authenticated DOM."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import re
from typing import Iterator
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import Page

from scout_agent.models.scout import Scout
from .base import PlatformAdapter


DODA_ORIGIN = "https://doda.jp"
OFFER_LIST_PATH = "/dcfront/referredJob/interviewOfferList/"
OFFER_DETAIL_PATHS = {
    "/dcfront/referredJob/interviewOfferDetail/",
    "/dcfront/referredJob/offerDetail/",
}
JOB_DETAIL_PATH = "/DodaFront/View/JobSearchDetail.action"
AGE_PATTERN = re.compile(r"受信日から\s*(\d+)\s*日経過")

# The detail filter is deliberately narrower than a keyword search. A tool
# mention (e.g. a Backend job using AWS) is not a Cloud/Infra main duty.
TARGET_ROLE = re.compile(
    r"\b(?:sre|devops)(?:\b|エンジニア)|"
    r"(?:cloud|クラウド|infrastructure|infra|インフラ|platform|プラットフォーム|"
    r"aws|network|ネットワーク|server(?![- ]?side)|サーバー(?!サイド))"
    r"[^。／/｜]{0,16}(?:engineer|architect|エンジニア|基盤|設計|構築|運用)|"
    r"(?:engineer|architect|エンジニア)[^。／/｜]{0,12}"
    r"(?:cloud|クラウド|infrastructure|infra|インフラ|platform|プラットフォーム|"
    r"aws|network|ネットワーク|server(?![- ]?side)|サーバー(?!サイド))",
    re.IGNORECASE,
)
TARGET_DUTY = re.compile(
    r"(?:cloud|クラウド|infrastructure|infra|インフラ|platform|プラットフォーム|"
    r"aws|network|ネットワーク|server(?![- ]?side)|サーバー(?!サイド)|\bsre\b|devops)"
    r"[^。\n]{0,45}(?:基盤|インフラ|環境|設計|構築|運用設計|信頼性改善)|"
    r"(?:基盤|インフラ|環境)[^。\n]{0,30}"
    r"(?:cloud|クラウド|aws|network|ネットワーク|server|サーバー)",
    re.IGNORECASE,
)
MAIN_DUTY_MARKER = re.compile(r"主な業務|主業務|主要業務|メイン|中心|主担当|担当|仕事内容|業務内容", re.IGNORECASE)
NON_IT_DETAIL_ROLES = (
    (re.compile(r"営業|セールス|ルームアドバイザー|PR担当|代理店サポート"), "営業・販売"),
    (re.compile(r"事務|オフィスワーク|アシスタント|秘書"), "事務・支援"),
    (re.compile(r"製造(?:スタッフ|オペレーター|職|作業)|工場作業"), "製造"),
    (re.compile(r"品質テスト|品質検査|品質管理|テストスタッフ"), "品質テスト"),
    (re.compile(r"研究職|研究者|化学・バイオ|バイオ系"), "研究"),
    (re.compile(r"物理シミュレーション|CAE解析|機械電気ソフト|機械・電気ソフト"), "物理仿真・机械电气开发"),
    (re.compile(r"警備|グランドハンドリング"), "警備・空港業務"),
    (re.compile(r"ドライバー|タクシー|送迎|運転手"), "驾驶・运输"),
    (re.compile(r"倉庫|物流|宅配"), "仓储・物流"),
    (re.compile(r"施工|管工事|建築工事"), "施工"),
    (re.compile(r"設備管理|設備メンテナンス|メンテナンスエンジニア"), "设备管理"),
    (re.compile(r"店舗運営|店長|生鮮売り場|生鮮部門|販売スタッフ|飲食店|"
                r"商業施設.{0,12}運営管理|ららぽーと.{0,12}運営管理"), "门店・商业设施运营"),
    (re.compile(r"マンション管理|不動産管理|物件管理"), "物业管理"),
    (re.compile(r"人材(?:管理|コーディネーター|紹介)|採用担当"), "人材"),
    (re.compile(r"教育(?:職|担当|事業|支援)|塾|講師|インストラクター|"
                r"個別指導学院|体験授業"), "教育"),
    (re.compile(r"eスポーツ|スポーツ総合職"), "体育・娱乐"),
    (re.compile(r"デザイナー|デザイン職|クリエイター"), "设计"),
)
NON_TARGET_IT_ROLES = (
    (re.compile(r"application|アプリケーション|アプリ.{0,20}開発|Webアプリ|Web開発|Webシステム", re.IGNORECASE), "Application/Web 开发"),
    (re.compile(r"システム開発|業務システム.{0,8}開発|情報システム.{0,8}開発", re.IGNORECASE), "System 开发"),
    (re.compile(r"backend|back-end|バックエンド|サーバーサイド", re.IGNORECASE), "Backend 开发"),
    (re.compile(r"frontend|front-end|フロントエンド", re.IGNORECASE), "Frontend 开发"),
    (re.compile(r"組込|組み込み|制御系ソフト|車載ソフト|モビリティ.{0,20}開発|ADAS.{0,30}開発|MaaS.{0,30}開発"), "組込/モビリティ 开发"),
    (re.compile(r"\bQA\b|テストエンジニア|品質保証|ソフトウェアテスト|システムテスト|テスター|ソフトウェア検証"), "QA/Test"),
    (re.compile(r"helpdesk|ヘルプデスク|テクサポ|テクニカルサポート|キッティング", re.IGNORECASE), "Helpdesk/技术支持"),
    (re.compile(r"IT業務支援|ITサポート業務|IT事務"), "IT 业务支持"),
)
CLIENT_SITE = re.compile(r"客先常駐|顧客先常駐|クライアント先常駐|就業先|派遣先")
CLIENT_SITE_NEGATION = re.compile(
    r"(?:客先|顧客先|クライアント先|就業先|派遣先).{0,8}(?:なし|無し|ではない|ありません|しない)"
)


def _job_outline(jd_text: str | None) -> str:
    if not jd_text:
        return ""
    match = re.search(
        r"^jobContentOutline:\s*(.*?)(?=^[A-Za-z][A-Za-z0-9]*:\s*|\Z)",
        jd_text, re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _non_target_main_duty(jd_text: str | None) -> str | None:
    outline = _job_outline(jd_text)
    for pattern, category in NON_TARGET_IT_ROLES:
        if pattern.search(outline):
            return category
    if not jd_text:
        return None
    for segment in re.split(r"[。\n]", jd_text):
        if MAIN_DUTY_MARKER.search(segment):
            for pattern, category in NON_TARGET_IT_ROLES:
                if pattern.search(segment):
                    return category
    return None


def _primary_target_duty(jd_text: str | None) -> bool:
    if not jd_text:
        return False
    if TARGET_DUTY.search(_job_outline(jd_text)):
        return True
    # A detail paragraph must explicitly frame target work as a responsibility;
    # an incidental technology list or collaboration with an SRE team is not enough.
    return any(
        MAIN_DUTY_MARKER.search(segment) and TARGET_DUTY.search(segment)
        for segment in re.split(r"[。\n]", jd_text)
        if not segment.startswith(("projectCase:", "developmentEnvironment:"))
    )


def doda_detail_prefilter(scout: Scout) -> str | None:
    """Return a Chinese reason for an evidenced 0-token skip, else None."""
    title = (scout.job_title or "").strip()
    if not title or TARGET_ROLE.search(title) or _primary_target_duty(scout.jd_text):
        return None
    for pattern, category in NON_TARGET_IT_ROLES:
        if pattern.search(title):
            return f"详情职位名称明确以{category}为主，未见 Cloud/Infra/Platform/SRE/DevOps 为主要职责的证据。"
    main_duty = _non_target_main_duty(scout.jd_text)
    if main_duty is not None:
        return f"结构化 JD 的主要工作职责明确为{main_duty}，未见 Cloud/Infra/Platform/SRE/DevOps 为主要职责的证据。"
    site_evidence = CLIENT_SITE.search(title) or CLIENT_SITE.search(_job_outline(scout.jd_text))
    if site_evidence and not CLIENT_SITE_NEGATION.search(title + " " + (scout.jd_text or "")):
        if re.search(r"社内SE|ITエンジニア|システムエンジニア|\bSE\b|開発エンジニア", title, re.IGNORECASE):
            return "详情明确为客先/就業先的非 Cloud/Infra 主职岗位。"
    # These broad titles cannot safely establish the primary duties by themselves.
    if re.search(r"ITエンジニア|社内SE|情報系エンジニア|ITサービスマネジメント", title, re.IGNORECASE):
        return None
    for pattern, category in NON_IT_DETAIL_ROLES:
        if pattern.search(title):
            return f"详情职位名称明确指向{category}，未见 Cloud/Infra/Platform/SRE/DevOps 为主要职责的证据。"
    return None


@dataclass(frozen=True)
class DodaOfferRef:
    external_id: str
    message_id: str
    job_id: str
    url: str
    company_name: str | None
    job_title: str | None  # List headline; actual occupation comes from the JD.
    age_days: int | None
    scout_kind: str


def doda_external_id(message_id: str, job_id: str) -> str:
    return f"offer:{message_id}:job:{job_id}"


def parse_doda_ref(
    href: str, *, card_id: str | None, company_name: str | None,
    job_title: str | None, age_label: str, premium: bool,
) -> DodaOfferRef:
    url = urljoin(DODA_ORIGIN, href)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "doda.jp" or parts.path not in OFFER_DETAIL_PATHS:
        raise ValueError("Unexpected doda offer detail link")
    params = parse_qs(parts.query)
    message_id = params.get("message_id", [])
    job_id = params.get("jid", [])
    if (len(message_id) != 1 or not message_id[0].isdigit()
            or len(job_id) != 1 or not job_id[0].isdigit()):
        raise ValueError("doda offer link lacks stable message_id or jid")
    if card_id and card_id != message_id[0]:
        raise ValueError("doda card ID and message_id disagree")
    age_match = AGE_PATTERN.search(age_label)
    return DodaOfferRef(
        external_id=doda_external_id(message_id[0], job_id[0]),
        message_id=message_id[0], job_id=job_id[0], url=url,
        company_name=company_name.strip() or None if company_name else None,
        job_title=job_title.strip() or None if job_title else None,
        age_days=int(age_match.group(1)) if age_match else None,
        scout_kind="premium_offer" if premium else "company_offer",
    )


def _plain(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li)>", "\n", value)
    text = re.sub(r"<[^>]*>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()) or None


def parse_doda_job_payload(payload: dict, *, expected_job_id: str) -> dict:
    """Extract fields from doda's observed Next.js pageProps.job.job object."""
    job = payload.get("props", {}).get("pageProps", {}).get("job", {}).get("job")
    if not isinstance(job, dict) or str(job.get("jid")) != expected_job_id:
        raise ValueError("doda JD job ID does not match offer link")
    recruit = job.get("recruit") or {}
    if not isinstance(recruit, dict):
        recruit = {}
    sections = (
        "jobContentOutline", "jobContentDetail", "targetMemberOutline", "targetMemberDetail",
        "projectCase", "developmentEnvironment", "workTime", "holiday",
    )
    jd = "\n".join(
        f"{name}: {text}" for name in sections
        if (text := _plain(recruit.get(name)))
    ) or None
    salary = _plain(recruit.get("salary"))
    location = "\n".join(
        text for key in ("jobState", "access") if (text := _plain(recruit.get(key)))
    ) or None
    return {
        "job_title": _plain(job.get("occupationName")),
        "company_name": _plain(job.get("corporateName")),
        "jd_text": jd,
        "salary_text": salary,
        "location_text": location,
    }


class DodaAdapter(PlatformAdapter):
    platform_name = "doda"
    # Observed sort_id=1 is 受信日が新しい順; default sort_id=7 is マッチ順.
    # A GET URL changes only this list view, not account settings.
    scout_list_url = f"{DODA_ORIGIN}{OFFER_LIST_PATH}?sort_id=1"

    def is_logged_in(self, page: Page | None) -> bool:
        return bool(
            page is not None
            and urlsplit(page.url).netloc == "doda.jp"
            and urlsplit(page.url).path == OFFER_LIST_PATH
            and page.locator("#formList").count()
            and page.get_by_role("heading", name="企業からのオファー").count()
        )

    def iter_scout_list(self, page: Page | None, *, limit: int = 100) -> Iterator[DodaOfferRef]:
        if page is None:
            raise ValueError("doda requires a browser page")
        emitted = 0
        seen_ids: set[str] = set()
        while emitted < limit:
            page.locator("#formList .layoutList01 h2.title a[href]").first.wait_for(timeout=10000)
            cards = page.locator("#formList .layoutList01")
            count = cards.count()
            for index in range(count):
                if emitted >= limit:
                    break
                card = cards.nth(index)
                link = card.locator("h2.title a[href]").first
                href = link.get_attribute("href")
                if not href:
                    continue
                try:
                    ref = parse_doda_ref(
                        href, card_id=card.get_attribute("id"),
                        company_name=card.locator("h2.title a span.company").first.inner_text(),
                        job_title=card.locator("h2.title a span.job").first.inner_text(),
                        age_label=card.locator(".box39").first.inner_text()
                        if card.locator(".box39").count() else "",
                        premium=card.get_by_role("heading", name="プレミアムオファー").count() > 0,
                    )
                except ValueError:
                    continue
                if ref.external_id in seen_ids:
                    continue
                seen_ids.add(ref.external_id)
                emitted += 1
                yield ref
            if emitted >= limit or count == 0:
                break
            current = page.locator("#page").input_value()
            if not current or not current.isdigit():
                break
            next_page = str(int(current) + 1)
            next_link = page.locator(f'a[name="paging"][data-value="{next_page}"]')
            if not next_link.count():
                break
            first_card_id = cards.first.get_attribute("id")
            next_link.first.click()  # Observed pager: navigation only, never an account action.
            page.wait_for_function(
                "({expected, previous}) => document.querySelector('#page')?.value === expected "
                "&& document.querySelector('#formList .layoutList01')?.id !== previous",
                arg={"expected": next_page, "previous": first_card_id}, timeout=10000,
            )

    def get_scout_list(self, page: Page | None, *, limit: int = 100) -> list[DodaOfferRef]:
        return list(self.iter_scout_list(page, limit=limit))

    def get_scout_detail(
        self, page: Page | None, ref: DodaOfferRef, *, max_age_days: int = 14,
    ) -> list[dict]:
        if page is None:
            raise ValueError("doda requires a browser page")
        detail = page.context.new_page()
        try:
            detail.goto(ref.url, wait_until="domcontentloaded", timeout=15000)
            parts = urlsplit(detail.url)
            if parts.netloc != "doda.jp" or parts.path not in OFFER_DETAIL_PATHS:
                raise RuntimeError("doda offer detail unavailable; left pending for retry")
            params = parse_qs(parts.query)
            if params.get("message_id") != [ref.message_id]:
                raise RuntimeError("doda offer message ID changed during navigation")
            detail.locator("article.main1024").wait_for(timeout=10000)
            scout_text = (
                detail.locator("article.main1024 p.preArea").first.inner_text().strip()
                if detail.locator("article.main1024 p.preArea").count() else None
            ) or None
            age_match = AGE_PATTERN.search(detail.locator("body").inner_text())
            age_days = int(age_match.group(1)) if age_match else ref.age_days
            if age_days is None or age_days > max_age_days:
                return []  # Date cannot be proven recent, or is explicitly too old.
            links = detail.locator('article.main1024 a[href*="JobSearchDetail.action"]')
            jobs: dict[str, str] = {}
            for href in links.evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))"):
                url = urljoin(DODA_ORIGIN, href or "")
                parsed = urlsplit(url)
                if parsed.netloc != "doda.jp" or parsed.path != JOB_DETAIL_PATH:
                    continue
                jid = parse_qs(parsed.query).get("jid", [])
                if len(jid) == 1 and jid[0].isdigit():
                    jobs.setdefault(jid[0], url)
            if ref.job_id not in jobs:
                raise RuntimeError("doda offer did not link its listed job ID; left pending")
            rows = []
            for jid, job_url in jobs.items():
                job_page = page.context.new_page()
                try:
                    job_page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
                    if urlsplit(job_page.url).netloc != "doda.jp":
                        raise RuntimeError("doda JD redirected outside doda; left pending")
                    job_page.locator("#__NEXT_DATA__").wait_for(state="attached", timeout=10000)
                    payload = json.loads(job_page.locator("#__NEXT_DATA__").text_content() or "{}")
                    fields = parse_doda_job_payload(payload, expected_job_id=jid)
                finally:
                    job_page.close()
                rows.append({
                    "id": doda_external_id(ref.message_id, jid),
                    "platform": "doda", "company_name": fields["company_name"] or ref.company_name,
                    "job_title": fields["job_title"] or ref.job_title,
                    "scout_title": ref.job_title, "scout_text": scout_text,
                    "jd_text": fields["jd_text"], "salary_text": fields["salary_text"],
                    "location_text": fields["location_text"], "url": job_url,
                    "scout_kind": ref.scout_kind, "sender_kind": "company",
                    "is_bulk_like": None, "received_at": None, "received_on": None,
                    "_age_days": age_days,
                })
            return rows
        finally:
            detail.close()

    def normalize_scout(self, raw: dict) -> Scout:
        return Scout.model_validate({key: value for key, value in raw.items() if not key.startswith("_")})
