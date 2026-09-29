"""Read-only マイナビ転職 corporate Scout adapter, based on the authenticated DOM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Iterator
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from .type_jp import TYPE_CLIENT_ASSIGNMENT, TYPE_INHOUSE_NO_CLIENT
from .doda import _job_outline, doda_detail_prefilter
from .base import PlatformAdapter


MYNAVI_ORIGIN = "https://tenshoku.mynavi.jp"
LIST_PATH = "/scout/messages/"
# The observed pagination uses order=2 (newest received first). This GET URL
# changes only the current view, not the account's saved settings.
LIST_URL = f"{MYNAVI_ORIGIN}{LIST_PATH}?filter=0&check=0&order=2"
JOB_PATH = re.compile(r"/jobinfo-(\d+-\d+-\d+-\d+)/")
RECEIVED_DATE = re.compile(r"受信日\s*[:：]\s*(\d{4})/(\d{1,2})/(\d{1,2})")

# Only the role and the structured job-content fields establish primary duties.
# In particular, an AWS tool mention in a Backend JD, or social infrastructure
# (water/gas/transport), is not evidence of IT infrastructure work.
MYNAVI_TARGET_WORK = re.compile(
    # Bare "cloud" may describe an application or SaaS product, not infra work.
    r"(?:(?:\bcloud\b|クラウド)(?:基盤|環境|インフラ|ネットワーク|サーバー|プラットフォーム)|"
    r"\b(?:aws|azure|gcp)\b\s*(?:基盤|環境|インフラ|の設計|の構築)|"
    r"\binfrastructure\b|\binfra\b|"
    r"ITインフラ|インフラ|\bplatform\b|プラットフォーム|\bsre\b|\bdevops\b|"
    r"\bnetwork\b|ネットワーク|\bserver\b|サーバー?(?!ー?サイド)|"
    r"(?<![A-Za-z])kubernetes(?![A-Za-z])|\bIaC\b)"
    r"[^、。／/\n]{0,25}(?:設計|構築|運用|保守|管理|改善|最適化|監視|障害対応|自動化|移行|リプレイス)",
    re.IGNORECASE,
)
MYNAVI_TARGET_ROLE = re.compile(
    r"(?:\bcloud\b|クラウド|\binfrastructure\b|\binfra\b|インフラ|"
    r"\bplatform\b|プラットフォーム|\bsre\b|\bdevops\b|\baws\b|"
    r"\bnetwork\b|ネットワーク|\bserver\b|サーバー?(?!ー?サイド))"
    r"[^。／/｜]{0,18}(?:engineer|architect|エンジニア|基盤|設計|構築|運用)",
    re.IGNORECASE,
)
MYNAVI_SOCIAL_INFRA = re.compile(r"水道|ガス|電力|道路|鉄道|交通|都市インフラ|社会インフラ")
MYNAVI_IT_INFRA = re.compile(
    r"ITインフラ|クラウド|\bcloud\b|\baws\b|\bplatform\b|"
    r"ネットワーク|\bnetwork\b|サーバー?(?!サイド)|\bserver\b|\bsre\b|\bdevops\b",
    re.IGNORECASE,
)
MYNAVI_NON_TARGET_MAIN = (
    (re.compile(r"ERP|CRM|SAP|Dynamics\s*365|Salesforce|Pega|ServiceNow|業務パッケージ", re.IGNORECASE), "ERP/CRM/SAP・业务软件"),
    (re.compile(r"\bBI\b|ダッシュボード|データ分析|データエンジニア", re.IGNORECASE), "BI/数据分析"),
    (re.compile(r"\bQA\b|品質保証|品質テスト|ソフトウェアテスト|システムテスト|テスター|検証業務", re.IGNORECASE), "QA/Test"),
    (re.compile(r"\bPMO?\b|プロジェクトマネージャー|プロダクトマネージャー|プロジェクト管理|マネジメント業務", re.IGNORECASE), "PM/PMO・项目管理"),
    (re.compile(r"ITコンサル|コンサルティング|ソリューション.{0,10}提案|提案活動|戦略の企画|グランドデザイン", re.IGNORECASE), "IT Consulting"),
    (re.compile(r"モバイルアプリ|スマホアプリ|Android|iOS|組込|組み込み|制御プログラム|車載|パチスロ", re.IGNORECASE), "Mobile/Embedded 开发"),
    (re.compile(r"backend|back-end|バックエンド|frontend|front-end|フロントエンド|サーバーサイド|サーバサイド", re.IGNORECASE), "Backend/Frontend 开发"),
    (re.compile(r"Web系|Webアプリ|Web開発|WEBエンジニア|Webエンジニア|ウェブアプリ|Webサイト|ECサイト|アプリケーション|アプリ開発", re.IGNORECASE), "Application/Web 开发"),
    (re.compile(r"(?:SaaS|クラウドサービス|クラウド認証システム).{0,35}(?:開発|企画)|"
                r"(?:SaaS|クラウドサービス).{0,12}エンジニア", re.IGNORECASE), "SaaS/Application 开发"),
    (re.compile(r"電子帳票システム.{0,100}(?:設定|試験|導入)", re.IGNORECASE | re.DOTALL),
     "业务系统导入/测试"),
    (re.compile(r"Java.{0,12}(?:開発プロジェクト|システム開発)", re.IGNORECASE),
     "Application/System 开发"),
    (re.compile(r"(?:顧客|クライアント).{0,30}開発チームをつなぎ|"
                r"開発チーム.{0,20}(?:進捗管理|品質確認)", re.IGNORECASE | re.DOTALL),
     "开发团队协调/进度管理"),
    (re.compile(r"システム開発|システム(?:等)?の(?:設計|開発|改修|保守|運用|導入)|システム(?:の)?設計.{0,6}開発|システムを(?:開発|導入)|業務システム|基幹システム|管理システム|パッケージ(?:システム|ソフト)|パッケージを.{0,35}(?:導入|開発)|ソフトウェア.{0,10}開発|ソフト開発|プログラムの開発|機能開発|サービス.{0,15}開発|製品.{0,15}開発|開発全工程|開発エンジニア|開発業務|POS開発|(?:設計|要件定義).{0,12}開発.{0,8}テスト", re.IGNORECASE), "System/Package 开发"),
)
MYNAVI_MANAGEMENT_ONLY = re.compile(
    r"PMO\s*として|プロジェクトで\s*PMO|"
    r"プロジェクト(?:の|での)(?:統括|管理|進捗管理)|"
    r"(?:開発|構築)作業は(?:基本)?なし|マネジメント業務を(?:担当|お任せ)",
    re.IGNORECASE,
)
MYNAVI_NON_IT_SERVICE_MAIN = (
    (
        re.compile(r"受付|コンシェルジュ|フロント(?!エンド)"),
        re.compile(r"お客様|ご来店|受付|接客|ご案内|販売|ショップ|カウンター"),
        "受付・接客",
    ),
    (
        re.compile(r"お部屋探し|住まい探し|物件(?:案内|紹介)|入居(?:者)?サポート|賃貸仲介|不動産(?:営業|案内|仲介)"),
        re.compile(r"(?:住まい|お部屋|物件|賃貸|間取り|家賃).{0,40}(?:探し|案内|サポート|紹介|提案|お客様)|"
                   r"(?:お客様|ご来店).{0,40}(?:住まい|お部屋|物件|賃貸|間取り|家賃)"),
        "不動産・賃貸の住まい探し支援",
    ),
    (
        re.compile(r"(?:店舗|店頭|カウンター).{0,8}(?:接客|案内|受付|販売|スタッフ)|"
                   r"(?:接客|販売).{0,8}(?:スタッフ|担当)|(?:顧客|お客様)案内"),
        re.compile(r"店舗|店頭|カウンター|接客|ご来店|お客様|販売|商品案内"),
        "店舗・カウンターでの接客・顧客案内",
    ),
)

MYNAVI_SES_SIGNALS = (
    (re.compile(r"案件.{0,12}選択(?:制)?|案件.{0,20}選(?:ぶ|べる|択)|"
                r"案件リスト.{0,35}選択", re.DOTALL), "案件選択"),
    (re.compile(r"単価連動|案件単価.{0,35}(?:給与|月給).{0,15}(?:決ま|連動)"), "単価連動"),
    (re.compile(r"還元率|(?:最大)?還元\s*\d{1,2}\s*[%％]"), "還元率"),
    (re.compile(r"会社都合.{0,18}アサイン(?:なし|しない|させない|はありません)"), "会社都合アサインなし"),
    (re.compile(r"営業.{0,35}(?:案件|プロジェクト).{0,35}(?:紹介|探)|"
                r"(?:案件|プロジェクト).{0,35}営業.{0,25}(?:紹介|探)", re.DOTALL),
     "営業案件紹介"),
    (re.compile(r"待機(?:時|中|期間).{0,15}(?:給与|月給).{0,12}(?:保証|減額なし|変更なし)"), "待機給与保証"),
)
MYNAVI_PROJECT_ASSIGNMENT = re.compile(
    r"(?:案件|プロジェクト)(?:に|へ|から)(?:アサイン|配属)|"
    r"(?:案件|プロジェクト)(?:リスト|一覧|プール).{0,60}(?:選択|紹介|配属)",
    re.DOTALL,
)


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _received_on(label: str | None) -> date | None:
    match = RECEIVED_DATE.search(label or "")
    if match is None:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


@dataclass(frozen=True)
class MynaviScoutRef:
    external_id: str
    job_id: str
    delivery_id: str
    url: str
    company_name: str | None
    scout_title: str | None
    received_on: date | None
    job_title: str | None = None  # The observed list has a message subject, not a job title.
    scout_kind: str = "corporate_scout"


def mynavi_external_id(delivery_id: str, job_id: str) -> str:
    return f"delivery:{delivery_id}:job:{job_id}"


def parse_mynavi_ref(
    href: str, *, checkbox_key: str | None, company_name: str | None,
    scout_title: str | None, received_label: str | None,
) -> MynaviScoutRef:
    url = urljoin(MYNAVI_ORIGIN, href)
    parts = urlsplit(url)
    match = JOB_PATH.fullmatch(parts.path)
    if parts.scheme != "https" or parts.netloc != "tenshoku.mynavi.jp" or match is None:
        raise ValueError("Unexpected マイナビ Scout job link")
    delivery = parse_qs(parts.query).get("deliveryId", [])
    if len(delivery) != 1 or not delivery[0].isdigit():
        raise ValueError("マイナビ Scout link lacks a stable deliveryId")
    job_id = match.group(1)
    # The observed checkbox key encodes the first three URL job components and
    # the delivery ID. Check it for one-job cards, but the URL remains the source
    # for each independent job if a card later contains multiple links.
    expected_key = "-".join([*job_id.split("-")[:3], delivery[0]])
    if checkbox_key is not None and checkbox_key != expected_key:
        raise ValueError("マイナビ checkbox key disagrees with job and delivery IDs")
    return MynaviScoutRef(
        external_id=mynavi_external_id(delivery[0], job_id),
        job_id=job_id, delivery_id=delivery[0], url=url,
        company_name=_clean(company_name), scout_title=_clean(scout_title),
        received_on=_received_on(received_label),
    )


def _table_value(page: Page, label: str) -> str | None:
    table = page.locator("table.jobOfferTable").first
    if not table.count():
        return None
    for row in table.locator("tr").all():
        heading = row.locator("th").first
        if heading.count() and heading.inner_text().strip() == label:
            cell = row.locator("td").first
            return _clean(cell.inner_text()) if cell.count() else None
    return None


def _mynavi_non_target_main(text: str) -> str | None:
    for pattern, category in MYNAVI_NON_TARGET_MAIN:
        if pattern.search(text):
            return category
    return None


def _mynavi_social_infra_only(text: str) -> bool:
    return bool(MYNAVI_SOCIAL_INFRA.search(text) and not MYNAVI_IT_INFRA.search(text))


def mynavi_primary_target_duty(jd_text: str | None) -> bool:
    """Require evidenced IT infrastructure work in the JD's primary-duty text."""
    outline = _job_outline(jd_text)
    if _mynavi_social_infra_only(outline):
        return False
    if MYNAVI_MANAGEMENT_ONLY.search(outline):
        return False
    if MYNAVI_TARGET_WORK.search(outline):
        return True
    # An explicit non-target outline takes precedence over incidental technology
    # and secondary responsibilities mentioned later in a long JD.
    if _mynavi_non_target_main(outline):
        return False
    if not jd_text:
        return False
    detail = re.search(
        r"^jobContentDetail:\s*(.*?)(?=^[A-Za-z][A-Za-z0-9]*:\s*|\Z)",
        jd_text, re.MULTILINE | re.DOTALL,
    )
    if not detail:
        return False
    # Only introductory responsibility statements count; later project examples,
    # environment lists, and desired skills are deliberately ignored.
    first_duties = " ".join(re.split(r"[。\n]", detail.group(1), maxsplit=2)[:2])
    return not _mynavi_social_infra_only(first_duties) and bool(MYNAVI_TARGET_WORK.search(first_duties))


def mynavi_detail_prefilter(scout: Scout) -> str | None:
    """Conservatively exclude evidenced non-target primary duties, never by company."""
    title = (scout.job_title or "").strip()
    outline = _job_outline(scout.jd_text)
    if mynavi_primary_target_duty(scout.jd_text):
        return None
    # A social/industrial use of インフラ is not an IT-infrastructure role.
    if MYNAVI_SOCIAL_INFRA.search(title + " " + outline):
        if "インフラ" in title + outline and not MYNAVI_IT_INFRA.search(title + " " + outline):
            if _mynavi_non_target_main(outline) or re.search(r"システム.{0,15}(?:設計|開発)", outline):
                return "JD 主要负责社会基础设施领域的业务系统开发，不是 IT Infrastructure 主职。"
    category = _mynavi_non_target_main(outline)
    if category:
        return f"结构化 JD 的主要工作职责明确为{category}，未见 Cloud/Infra/Platform/SRE/DevOps 为主要职责的证据。"
    category = _mynavi_non_target_main(title)
    if category:
        return f"详情职位名称明确以{category}为主，JD 未显示 Cloud/Infra/Platform/SRE/DevOps 为主要职责。"
    if title and MYNAVI_TARGET_ROLE.search(title):
        return None
    # A service-role title alone is not enough: the structured JD outline must
    # independently describe customer-facing, non-IT primary duties.
    for role_pattern, duty_pattern, category in MYNAVI_NON_IT_SERVICE_MAIN:
        if role_pattern.search(title) and duty_pattern.search(outline):
            return f"结构化 JD 的主要工作职责明确为{category}，未见 IT Cloud/Infrastructure 主职证据。"
    return doda_detail_prefilter(scout)


def mynavi_ses_hard_rule(scout: Scout) -> Evaluation | None:
    """Require combined placement and SES business evidence, or multiple signals."""
    jd = scout.jd_text or ""
    if not jd:
        return None
    outline = _job_outline(jd)
    location = scout.location_text or ""
    if TYPE_INHOUSE_NO_CLIENT.search(" ".join((scout.job_title or "", outline, location))):
        return None
    site = TYPE_CLIENT_ASSIGNMENT.search(" ".join((location, outline)))
    placement = MYNAVI_PROJECT_ASSIGNMENT.search(outline)
    signals = [name for pattern, name in MYNAVI_SES_SIGNALS
               if pattern.search(" ".join((jd, scout.salary_text or "")))]
    if not ((site or placement) and signals or len(signals) >= 2):
        return None
    evidence = ["客先/项目现场配属" if site else "案件/项目配属" if placement else "多项案件制商业模式"]
    evidence.extend(signals)
    return Evaluation(
        verdict="SKIP", confidence=0.9,
        summary="职位的项目配属与强案件制证据触发非 SES/非客先常驻硬红线。",
        reasons=[f"本地证据：{'、'.join(evidence)}。"],
        concerns=["未将受託、直請け、チーム参画或普通的『案件』单独视为 SES。"],
        client_site=True if site else None,
    )


class MynaviAdapter(PlatformAdapter):
    platform_name = "mynavi"
    scout_list_url = LIST_URL

    def is_logged_in(self, page: Page | None) -> bool:
        return bool(
            page is not None
            and urlsplit(page.url).netloc == "tenshoku.mynavi.jp"
            and urlsplit(page.url).path == LIST_PATH
            and page.get_by_role("heading", name="企業からのスカウト受信一覧").count()
        )

    def iter_scout_list(self, page: Page | None, *, limit: int = 100) -> Iterator[MynaviScoutRef]:
        if page is None:
            raise ValueError("マイナビ requires a browser page")
        emitted = 0
        seen: set[str] = set()
        while emitted < limit:
            if not self.is_logged_in(page):
                raise RuntimeError("マイナビ Scout list unavailable; log in manually")
            cards = page.locator(".scoutMailBox__item")
            count = cards.count()
            if count == 0:
                break
            for card in cards.all():
                if emitted >= limit:
                    break
                links = card.locator("a.scoutMailBox__detailLink[href]")
                if not links.count():
                    raise RuntimeError("マイナビ Scout card lacks a job link")
                checkbox = card.locator('input[name="keyList"]')
                checkbox_key = checkbox.first.get_attribute("value") if checkbox.count() == 1 and links.count() == 1 else None
                company = card.locator(".scoutMailBox__text").first
                subject = card.locator(".scoutMailBox__title").first
                received = card.locator(".scoutMailBox__detailHeader .date").first
                for link in links.all():
                    if emitted >= limit:
                        break
                    ref = parse_mynavi_ref(
                        link.get_attribute("href") or "", checkbox_key=checkbox_key,
                        company_name=company.inner_text() if company.count() else None,
                        scout_title=subject.inner_text() if subject.count() else None,
                        received_label=received.inner_text() if received.count() else None,
                    )
                    if ref.external_id in seen:
                        continue
                    seen.add(ref.external_id)
                    emitted += 1
                    yield ref
            if emitted >= limit:
                break
            current = parse_qs(urlsplit(page.url).query).get("pageNo", ["1"])
            current_page = int(current[0]) if len(current) == 1 and current[0].isdigit() else 1
            next_url = None
            for href in page.locator('a[href*="pageNo"]').evaluate_all(
                "elements => elements.map(element => element.getAttribute('href'))"
            ):
                url = urljoin(MYNAVI_ORIGIN, href or "")
                parts = urlsplit(url)
                number = parse_qs(parts.query).get("pageNo", [])
                if (parts.scheme == "https" and parts.netloc == "tenshoku.mynavi.jp"
                        and parts.path == LIST_PATH and number == [str(current_page + 1)]):
                    next_url = url
                    break
            if next_url is None:
                break
            try:
                page.goto(next_url, wait_until="commit", timeout=30000)
            except PlaywrightTimeoutError:
                # A second read-only GET is safe when this site's navigation
                # stalls; a repeated failure still stops the scan explicitly.
                page.goto(next_url, wait_until="commit", timeout=30000)
            page.get_by_role("heading", name="企業からのスカウト受信一覧").wait_for(timeout=10000)
            page.locator(".scoutMailBox__item").first.wait_for(timeout=10000)

    def get_scout_list(self, page: Page | None, *, limit: int = 100) -> list[MynaviScoutRef]:
        return list(self.iter_scout_list(page, limit=limit))

    def get_scout_detail(
        self, page: Page | None, ref: MynaviScoutRef, *, max_age_days: int = 14,
    ) -> list[dict]:
        if page is None:
            raise ValueError("マイナビ requires a browser page")
        detail = page.context.new_page()
        try:
            # Waiting for the response commit avoids unrelated page resources
            # holding up a read-only detail fetch; required DOM is awaited below.
            detail.goto(ref.url, wait_until="commit", timeout=20000)
            parts = urlsplit(detail.url)
            match = JOB_PATH.fullmatch(parts.path)
            delivery = parse_qs(parts.query).get("deliveryId", [])
            if (parts.netloc != "tenshoku.mynavi.jp" or match is None
                    or match.group(1) != ref.job_id or delivery != [ref.delivery_id]):
                raise RuntimeError("マイナビ Scout detail changed job or delivery ID; left pending")
            detail.locator("h1 .occName").first.wait_for(timeout=10000)
            info = detail.locator(".scoutDetailInfoArea .scoutDetailInfo").first
            info.wait_for(timeout=10000)
            detail_date = _received_on(
                info.locator(".scoutDetailInfo__detailHeader").first.inner_text()
            )
            if ref.received_on and detail_date and ref.received_on != detail_date:
                raise RuntimeError("マイナビ list/detail received dates disagree; left pending")
            received_on = detail_date or ref.received_on
            if received_on is None or (datetime.now(ZoneInfo("Asia/Tokyo")).date() - received_on).days > max_age_days:
                return []
            title = _clean(detail.locator("h1 .occName").first.inner_text())
            company = _clean(detail.locator("h1 .companyName").first.inner_text())
            description = detail.locator(".jobPointArea__wrap-jobDescription").first
            description.wait_for(timeout=10000)
            outline = description.locator(".jobPointArea__head").first
            bodies = description.locator(".jobPointArea__body")
            jd_parts = []
            if outline.count() and (value := _clean(outline.inner_text())):
                jd_parts.append(f"jobContentOutline: {value}")
            detail_text = _clean("\n".join(body.inner_text() for body in bodies.all()))
            if detail_text:
                jd_parts.append(f"jobContentDetail: {detail_text}")
            if not jd_parts:
                raise RuntimeError("マイナビ job description unavailable; left pending")
            requirements = detail.locator(".jobPointArea__body--large").first
            if requirements.count() and (value := _clean(requirements.inner_text())):
                jd_parts.append(f"targetMemberDetail: {value}")
            salary = _table_value(detail, "給与")
            location = _table_value(detail, "勤務地")
            body = info.locator(".scoutDetailInfo__body").first
            scout_text = _clean(body.text_content()) if body.count() else None
            return [{
                "id": ref.external_id, "platform": "mynavi",
                "company_name": company or ref.company_name, "job_title": title,
                "scout_title": ref.scout_title, "scout_text": scout_text,
                "jd_text": "\n".join(jd_parts), "salary_text": salary,
                "location_text": location, "url": ref.url,
                "received_on": received_on, "received_at": None,
                "scout_kind": ref.scout_kind, "sender_kind": "company",
                "is_bulk_like": None,
            }]
        finally:
            detail.close()

    def normalize_scout(self, raw: dict) -> Scout:
        return Scout.model_validate(raw)
