from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from scout_agent.models.scout import Scout
from .base import PlatformAdapter


GREEN_ORIGIN = "https://www.green-japan.com"
THREAD_PATH = re.compile(r"/messages/v2/(\d+)")
JOB_PATH = re.compile(r"/company/\d+/job/\d+")
RECEIVED_AT = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}$")
SALARY = re.compile(r"\d+\s*万円")
LOCATION = re.compile(r"都|道|府|県|全国|海外|リモート")
MAX_SCOUTS = 30


@dataclass(frozen=True)
class GreenScoutRef:
    external_id: str
    url: str
    company_name: str | None


def parse_scout_ref(href: str, card_text: str) -> GreenScoutRef:
    """Parse the observed /messages/v2/[threadId]?threadId=123 link."""
    parsed = urlsplit(urljoin(GREEN_ORIGIN, href))
    values = parse_qs(parsed.query).get("threadId", [])
    if parsed.netloc != "www.green-japan.com" or not parsed.path.startswith("/messages/v2/"):
        raise ValueError("Unexpected Green Scout link")
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError("Green Scout link has no numeric threadId")
    external_id = values[0]
    lines = [line.strip() for line in card_text.splitlines() if line.strip()]
    company_name = lines[1] if len(lines) > 1 and lines[0] == "スカウト" else None
    return GreenScoutRef(
        external_id=external_id,
        url=f"{GREEN_ORIGIN}/messages/v2/{external_id}?threadId={external_id}",
        company_name=company_name,
    )


def parse_received_at(value: str | None) -> datetime | None:
    if not value or not RECEIVED_AT.fullmatch(value.strip()):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M").replace(
            tzinfo=ZoneInfo("Asia/Tokyo")
        )
    except ValueError:
        return None


def parse_job_card(paragraphs: list[str]) -> tuple[str | None, str | None, str | None]:
    """Observed job link contains title, annual salary, then location paragraphs."""
    parts = [part.strip() for part in paragraphs]
    title = parts[0] or None if parts else None
    salary = parts[1] if len(parts) > 1 and SALARY.search(parts[1]) else None
    location = parts[2] if len(parts) > 2 and LOCATION.search(parts[2]) else None
    return title, salary, location


class GreenAdapter(PlatformAdapter):
    """READ-ONLY AUTOMATION: navigate only to observed Scout and job URLs."""

    platform_name = "green"
    scout_list_url = f"{GREEN_ORIGIN}/messages/v2"

    @staticmethod
    def _scout_cards(page: Page):
        # Exact Scout badge observed inside each thread link. No CSS class or nth-child.
        return page.locator('a[href*="/messages/v2/"]:visible').filter(
            has=page.locator('p:text-is("スカウト")')
        )

    def is_logged_in(self, page: Page | None) -> bool:
        if page is None or urlsplit(page.url).netloc != "www.green-japan.com":
            return False
        try:
            page.locator('a[role="tab"][href="/messages/v2"]').wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            return False
        return True

    def get_scout_list(self, page: Page | None) -> list[GreenScoutRef]:
        if page is None:
            raise ValueError("Green requires a browser page")
        cards = self._scout_cards(page)
        try:
            cards.first.wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            return []

        # Green's observed list is an infinite-scroll container. Scrolling only
        # reads more existing threads; it does not perform an account write.
        all_threads = page.locator('a[href*="/messages/v2/"]:not([role="tab"]):visible')
        while cards.count() < MAX_SCOUTS:
            before = all_threads.count()
            if before == 0:
                break
            try:
                all_threads.last.scroll_into_view_if_needed(timeout=5000)
            except PlaywrightTimeoutError:
                break
            try:
                all_threads.nth(before).wait_for(state="attached", timeout=3000)
            except PlaywrightTimeoutError:
                break

        refs: list[GreenScoutRef] = []
        for card in cards.all()[:MAX_SCOUTS]:
            href = card.get_attribute("href")
            if href is None:
                continue
            refs.append(parse_scout_ref(href, card.inner_text()))
        return refs

    def get_scout_detail(self, page: Page | None, scout: GreenScoutRef) -> dict:
        if page is None:
            raise ValueError("Green requires a browser page")
        page.goto(scout.url, wait_until="domcontentloaded", timeout=15000)
        match = THREAD_PATH.fullmatch(urlsplit(page.url).path)
        if not match or match.group(1) != scout.external_id:
            raise ValueError("Green detail URL does not match the Scout thread ID")

        job_link = page.locator('a[href*="/company/"][href*="/job/"]').first
        try:
            job_link.wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            job_link = None

        scout_text = None
        received_at = None
        job_title = None
        salary_text = None
        location_text = None
        jd_text = None
        if job_link is not None:
            job_title, salary_text, location_text = parse_job_card(
                job_link.locator("p").all_inner_texts()
            )
            panel = job_link.locator("xpath=../..")
            dates = panel.locator("p").filter(has_text=RECEIVED_AT)
            try:
                dates.first.wait_for(timeout=10000)
            except PlaywrightTimeoutError:
                pass
            if dates.count() == 1:
                bodies = dates.first.locator("xpath=preceding-sibling::div/p")
                if bodies.count() == 1:
                    scout_text = bodies.first.inner_text().strip() or None
                    received_at = parse_received_at(dates.first.inner_text())

            href = job_link.get_attribute("href")
            job_url = urljoin(GREEN_ORIGIN, href) if href else None
            job_parts = urlsplit(job_url) if job_url else None
            if job_parts and job_parts.netloc == "www.green-japan.com" and JOB_PATH.fullmatch(job_parts.path):
                job_page = page.context.new_page()
                try:
                    job_page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
                    heading = job_page.get_by_role("heading", name="仕事内容", exact=True)
                    heading.first.wait_for(timeout=10000)
                    title = job_page.locator("h1").first
                    if title.count():
                        job_title = title.inner_text().strip() or job_title
                    # The observed grandparent contains the job's overview,
                    # description and requirements, excluding recommendations.
                    jd_text = heading.first.locator("xpath=../..").inner_text().strip() or None
                except PlaywrightError:
                    # A missing public job page does not discard the Scout.
                    pass
                finally:
                    job_page.close()

        return {
            "id": scout.external_id,
            "platform": self.platform_name,
            "company_name": scout.company_name,
            "job_title": job_title,
            "scout_title": None,  # No reliable subject field observed.
            "scout_text": scout_text,
            "jd_text": jd_text,
            "salary_text": salary_text,
            "location_text": location_text,
            "url": scout.url,
            "received_at": received_at,
        }

    def normalize_scout(self, raw: dict) -> Scout:
        return Scout.model_validate(raw)
