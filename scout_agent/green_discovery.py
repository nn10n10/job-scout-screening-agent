"""Read-only Green discovery and model-free live probe."""
from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from scout_agent.platforms.green import JOB_PATH, parse_job_card
from scout_agent.search_platforms import FIELDS, Job

ORIGIN = 'https://www.green-japan.com'
SOURCES = {
    'AWS': '/search/skill/AWS', 'SRE': '/search/skill/SRE',
    'DevOps': '/search/skill/DevOps', 'Terraform': '/search/skill/Terraform',
    'Kubernetes': '/search/skill/Kubernetes', 'インフラエンジニア': '/jobtype-l/190150/01',
}
KEYWORDS = tuple(SOURCES)
SAFE_REASONS = frozenset({
    'SOURCE_URL_MISMATCH', 'JOB_URL_MISMATCH', 'NO_VALID_JOB_LINKS',
    'UNKNOWN_RESULT_COUNT', 'TITLE_MISSING', 'RESPONSIBILITIES_MISSING',
    'PLAYWRIGHT_TIMEOUT', 'PLAYWRIGHT_ERROR', 'PARSE_ERROR', 'UNKNOWN',
})
NUMBER = r'[0-9０-９][0-9０-９,，]*'
RESULT_COUNT = re.compile(
    rf'^\s*(?:(?:(?:検索結果|求人(?:数)?|該当(?:する)?求人(?:数)?|全|合計)\s*[:：]?\s*)?'
    rf'{NUMBER}\s*件(?:ヒットしました。|の求人|の検索結果)?|'
    rf'検索結果\s*{NUMBER}\s*企業\s*{NUMBER}\s*求人|{NUMBER}\s*求人)\s*$'
)


class Stage(str, Enum):
    CDP_CONNECT = 'CDP_CONNECT'
    SOURCE_NAVIGATION = 'SOURCE_NAVIGATION'
    SOURCE_URL = 'SOURCE_URL'
    JOB_LINKS = 'JOB_LINKS'
    RESULT_COUNT = 'RESULT_COUNT'
    DETAIL_NAVIGATION = 'DETAIL_NAVIGATION'
    DETAIL_TITLE = 'DETAIL_TITLE'
    DETAIL_RESPONSIBILITIES = 'DETAIL_RESPONSIBILITIES'


@contextmanager
def safety_stage(stage):
    try:
        yield
    except GreenSearchDOMPending:
        raise
    except (PlaywrightError, ValueError) as exc:
        reason = ('PLAYWRIGHT_TIMEOUT' if isinstance(exc, PlaywrightTimeoutError) else
                  'PLAYWRIGHT_ERROR' if isinstance(exc, PlaywrightError) else 'PARSE_ERROR')
        raise GreenSearchDOMPending(stage, reason) from None


def valid_source_redirect(url, label, page=1):
    if not isinstance(url, str) or re.search(r'[\s\x00-\x1f\x7f]', url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if (label not in SOURCES or page < 1 or parts.scheme != 'https'
            or parts.netloc != 'www.green-japan.com'):
        return False
    if parts.path == '/search':
        # Authenticated Green canonicalizes verified routes to opaque queries.
        # Do not infer, retain or print the meaning of other query parameters.
        if not parts.query:
            return False
    elif parts.path not in (SOURCES[label], SOURCES[label] + '/'):
        return False
    if page >= 2:
        pages = [value for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if key == 'page']
        if not pages or any(value != str(page) for value in pages):
            return False
    return True






def pagination_url(base, page):
    parts = urlsplit(base)
    if (parts.scheme != 'https' or parts.netloc != 'www.green-japan.com'
            or parts.path not in SOURCES.values() or parts.fragment or page < 1):
        raise ValueError('搜索来源 URL 或页码不在白名单，已停止。')
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'page']
    if page >= 2:
        query.append(('page', str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def source_url(label, page=1):
    if label not in SOURCES:
        raise ValueError('请选择已验证的 Green source label。')
    return pagination_url(ORIGIN + SOURCES[label], page)


class GreenSearchDOMPending(RuntimeError):
    def __init__(self, stage, reason):
        self.stage, self.reason = Stage(stage), reason
        super().__init__(f'Green 安全检查失败：stage={self.stage.value} reason={reason}')


class GreenSearchAdapter:
    platform_key = "green"
    source_labels = KEYWORDS

    def source_url(self, label, page=1):
        return source_url(label, page)

    def validate_job(self, job):
        checked = Job.from_url(job.url, {})
        if job.platform != self.platform_key or checked.job_id != job.job_id:
            raise ValueError("职位平台或 ID 不匹配，已安全停止。")
        return checked.url

    def ensure_verified(self):
        """Static safety implementation is enabled; this is not live verification."""
        return None

    def search_cards(self, keyword, page):
        target = source_url(keyword, page)
        self.last_stats = dict(cards=0, valid_job_links=0, title=0, salary=0, location=0)
        with safety_stage(Stage.SOURCE_NAVIGATION):
            self.page.goto(target, wait_until='domcontentloaded', timeout=15000)
        with safety_stage(Stage.SOURCE_URL):
            if not valid_source_redirect(self.page.url, keyword, page):
                raise GreenSearchDOMPending(Stage.SOURCE_URL, 'SOURCE_URL_MISMATCH')
        with safety_stage(Stage.JOB_LINKS):
            anchors = self.page.locator('a[href*="/company/"][href*="/job/"]:visible')
            try:
                anchors.first.wait_for(state='visible', timeout=10000)
            except PlaywrightTimeoutError:
                # An explicit zero count is still required below; no DOM fallback.
                pass
            jobs = {}
            valid = 0
            nodes = anchors.all()
            for anchor in nodes:
                href = anchor.get_attribute('href')
                if not href:
                    continue
                try:
                    job = Job.from_url(href, {})
                except ValueError:
                    continue
                valid += 1
                title, salary, location = parse_job_card(anchor.locator('p:visible').all_inner_texts())
                job.fields = {'title': title or '', 'salary': salary or '', 'location': location or ''}
                if job.job_id not in jobs:
                    jobs[job.job_id] = job
                else:
                    for key, value in job.fields.items():
                        if value and not jobs[job.job_id].fields.get(key):
                            jobs[job.job_id].fields[key] = value
        # Read only explicit result-count text nodes, never body/full-page fallback.
        self.last_stats = {'cards': len(nodes), 'valid_job_links': valid,
                           **{key: sum(bool(j.fields.get(key)) for j in jobs.values())
                              for key in ('title', 'salary', 'location')}}
        with safety_stage(Stage.RESULT_COUNT):
            counts = self.page.get_by_text(RESULT_COUNT).all_inner_texts()
            numbers = [int(value.replace(',', '').replace('，', '')) for text in counts
                       if RESULT_COUNT.fullmatch(text)
                       for value in re.findall(rf'({NUMBER})\s*(?:件|求人)', text)]
        if not jobs and (not numbers or any(numbers)):
            raise GreenSearchDOMPending(Stage.JOB_LINKS, 'NO_VALID_JOB_LINKS' if numbers else 'UNKNOWN_RESULT_COUNT')
        return list(jobs.values())

    def job_detail(self, job):
        return read_green_detail(self.page, job)


def parse_job_sections(text, *, company='', title='', salary='', location=''):
    aliases = {'仕事内容': 'responsibilities', '必須': 'required', '応募資格': 'required',
               '必須要件': 'required', '歓迎': 'preferred', '歓迎要件': 'preferred',
               '技術': 'technology', '開発環境': 'technology', '給与': 'salary',
               '勤務地': 'location', 'リモート': 'remote'}
    fields = {'company': company, 'title': title, 'salary': salary, 'location': location}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line in aliases:
            section = aliases[line]
        elif line in {'会社概要', 'おすすめの求人', '応募する', '気になる', 'ナビゲーション', '福利厚生'}:
            section = None
        elif section and line:
            fields[section] = fields.get(section, '') + '\n' + line
    return {key: '\n'.join(dict.fromkeys(' '.join(line.split()) for line in value.splitlines() if line.strip()))
            for key, value in fields.items() if value.strip()}


def read_green_detail(page, job):
    with safety_stage(Stage.DETAIL_NAVIGATION):
        checked = Job.from_url(job.url, job.fields)
        page.goto(checked.url, wait_until='domcontentloaded', timeout=15000)
        if Job.from_url(page.url, {}).job_id != job.job_id:
            raise GreenSearchDOMPending(Stage.DETAIL_NAVIGATION, 'JOB_URL_MISMATCH')
    with safety_stage(Stage.DETAIL_TITLE):
        title = page.locator('h1').first.inner_text().strip()
        if not title:
            raise GreenSearchDOMPending(Stage.DETAIL_TITLE, 'TITLE_MISSING')
    with safety_stage(Stage.DETAIL_RESPONSIBILITIES):
        heading = page.get_by_role('heading', name='仕事内容', exact=True).first
        heading.wait_for(timeout=10000)
        text = heading.locator('xpath=../..').inner_text()
        fields = parse_job_sections(text, company=job.fields.get('company', ''), title=title,
                                    salary=job.fields.get('salary', ''), location=job.fields.get('location', ''))
        if not fields.get('responsibilities'):
            raise GreenSearchDOMPending(Stage.DETAIL_RESPONSIBILITIES, 'RESPONSIBILITIES_MISSING')
    return fields


def probe_command(args, settings):
    from scout_agent.browser.manager import BrowserManager
    from scout_agent.browser.manager import CDPConnectionError
    label, adapter = 'NONE', None
    stage, reason = Stage.CDP_CONNECT, 'INVALID_CONFIGURATION'
    try:
        if settings.browser_mode != 'cdp':
            raise ValueError('Green probe 仅支持 BROWSER_MODE=cdp；请连接已登录 Chrome。')
        labels = args.keyword or KEYWORDS
        for candidate in labels:
            source_url(candidate)
        label = labels[0]
        limit = args.max_jobs if args.max_jobs is not None else 5
        if not 1 <= limit <= 5:
            raise ValueError('probe --max-jobs 必须为 1～5，以限制只读详情检查。')
        stage, reason = Stage.CDP_CONNECT, 'CDP_UNAVAILABLE'
        seen, parsed, checked = set(), 0, 0
        with BrowserManager(settings.profile_path, mode='cdp', cdp_endpoint=settings.cdp_endpoint).open() as session:
            if not session.contexts:
                raise ValueError('Chrome 无可用 context；请先打开已登录的 Green。')
            page = session.contexts[0].new_page()
            try:
                adapter = GreenSearchAdapter()
                adapter.page = page
                for label in labels:
                    cards = adapter.search_cards(label, 1)
                    details = 0
                    for job in cards:
                        if job.job_id in seen:
                            continue
                        seen.add(job.job_id)
                        checked += 1
                        fields = adapter.job_detail(job)
                        details += bool(fields.get('title') and fields.get('responsibilities'))
                        if checked >= limit:
                            break
                    parsed += details
                    print('source=' + label + ' ' + ' '.join(f'{key}={value}' for key, value in adapter.last_stats.items())
                          + f' detail_responsibilities={details}')
                    if checked >= limit:
                        break
            finally:
                # Keep the diagnostic for the failing read if cleanup also fails.
                active_error = sys.exc_info()[0] is not None
                try:
                    page.close()
                except PlaywrightError:
                    if not active_error:
                        raise
        if not seen or not parsed:
            stage, reason = Stage.JOB_LINKS, 'NO_PARSEABLE_JOBS'
            raise ValueError('probe 未找到可安全解析的职位；请检查 Green source 页面与登录状态，再更新 DOM 解析器。')
        print('probe 成功：只读结构检查通过，0 model call；未写数据库或报告。')
        return 0
    except (ValueError, GreenSearchDOMPending, PlaywrightError, CDPConnectionError) as exc:
        if isinstance(exc, GreenSearchDOMPending):
            stage, reason = exc.stage, exc.reason
        elif isinstance(exc, PlaywrightError):
            reason = 'PLAYWRIGHT_TIMEOUT' if isinstance(exc, PlaywrightTimeoutError) else 'PLAYWRIGHT_ERROR'
        stats = adapter.last_stats if adapter and hasattr(adapter, 'last_stats') else {}
        print(f'Green probe 失败：source={label} stage={stage.value} reason={reason} '
              + ' '.join(f'{key}={value}' for key, value in stats.items())
              + '；请检查对应阶段的 CDP 连接或 Green 页面结构，DOM 变化需更新解析器。', file=sys.stderr)
        return 1
