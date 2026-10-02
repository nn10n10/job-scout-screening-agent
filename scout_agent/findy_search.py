"""Read-only broad Findy search using observed routes and semantic labels."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from scout_agent.findy_structure import STRUCTURE, sanitize
from scout_agent.green_discovery import GreenSearchDOMPending, Stage
from scout_agent.platform_discovery import SNAPSHOT, login_url
from scout_agent.search_platforms import Job, job_identity

ORIGIN = 'https://findy-code.io'
SOURCES = ('おすすめ求人',)
JOB_PATH = re.compile(r'/companies/([0-9]+)/jobs/([A-Za-z0-9_-]+)')
ALIASES = {
    '仕事内容': 'responsibilities',
    '応募資格': 'required', '必須要件': 'required', '必須スキル': 'required',
    '歓迎要件': 'preferred', '歓迎スキル': 'preferred',
    '開発環境': 'technology', '技術': 'technology',
    '給与': 'salary', '年収': 'salary', '勤務地': 'location',
}
BOUNDARIES = (*ALIASES, '勤務時間', '福利厚生', '雇用形態')


def job_from_url(url, fields):
    if not isinstance(url, str) or not url or re.search(r'[\s\x00-\x1f\x7f]', url):
        raise ValueError('Findy 职位 URL 无效')
    parts = urlsplit(ORIGIN + url if url.startswith('/') and not url.startswith('//') else url)
    match = JOB_PATH.fullmatch(parts.path)
    if parts.scheme != 'https' or parts.netloc != 'findy-code.io' or not match or '?' in url or '#' in url:
        raise ValueError('Findy 职位 URL 不在白名单')
    external_id = f'{match[1]}:{match[2]}'
    return Job(job_identity('findy', external_id), ORIGIN + parts.path,
               fields, platform='findy', external_job_id=external_id)


def parse_fields(sections, title=''):
    fields = {'title': title.strip()} if title.strip() else {}
    for label, value in sections.items():
        key = ALIASES.get(label)
        if key and isinstance(value, str) and value.strip():
            fields[key] = value.strip()
    return fields


# Semantic HTML only: no site CSS selectors, body fallback, clicks or form submission.
DETAIL = r"""() => {
 const labels = LABELS_JSON;
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const headings = 'h1,h2,h3,h4,h5,h6,dt,th,[role=heading],section';
 const carriers = headings + ',label,p,span,div,strong';
 const text = e => (e.innerText || e.textContent || '').trim();
 const titleNode = Array.from(document.querySelectorAll('h1')).find(e => visible(e) && text(e));
 const title = titleNode ? text(titleNode) : '';
 const sections = {};
 const labelOf = e => visible(e) && e.matches(carriers) && labels.includes(text(e)) ? text(e) : '';
 // Local text only; nested labels and heading/section boundaries stop traversal.
 const collect = (node, label, texts) => {
   if (!visible(node)) return false;
   if (labelOf(node) || node.matches(headings)) return true;
   // Read local text nodes in order, including text beside inline children.
   for (const child of node.childNodes) {
     if (child.nodeType === 3) {
       const value = (child.textContent || '').trim();
       if (labels.includes(value)) return true;
       if (value) texts.push(value);
     } else if (child.nodeType === 1 && collect(child, label, texts)) return true;
   }
   return false;
 };
 for (const e of document.querySelectorAll(carriers)) {
   if (e === titleNode) continue;
   const label = labelOf(e);
   if (!label) continue;
   if (Array.from(e.children || []).some(child => labelOf(child) === label)) continue;
   const texts = [];
   for (let n = e.nextElementSibling; n; n = n.nextElementSibling) {
     if (collect(n, label, texts) || e.matches('th')) break;
   }
   // Wrapped labels may have their content beside an ancestor. Inspect only
   // one adjacent subtree per level, never the ancestor's aggregate contents.
   if (!texts.length) {
     let ancestor = e;
     for (let depth = 1; depth <= 3; depth++) {
       ancestor = ancestor.parentElement;
       if (!ancestor || ancestor.matches('body,html')) break;
       const sibling = ancestor.nextElementSibling;
       if (!sibling || !visible(sibling)) continue;
       collect(sibling, label, texts);
       if (texts.length) break;
     }
   }
   const value = texts.filter(Boolean).join('\n');
   if (value && !sections[label]) sections[label] = value;
 }
 return {title, sections};
}""".replace("LABELS_JSON", json.dumps(list(BOUNDARIES), ensure_ascii=False))


class FindySearchAdapter:
    last_safe_detail_diagnostic = None
    platform_key = 'findy'
    source_labels = SOURCES
    supports_pagination = True

    def ensure_verified(self):
        # Enabled by supervisor route/probe evidence; live parsing remains to be verified.
        pass

    def source_url(self, label, page=1):
        if label not in SOURCES or type(page) is not int or page < 1:
            raise ValueError('Findy 搜索来源或页码无效')
        return ORIGIN + '/recommends' + (f'?page={page}' if page >= 2 else '')

    def validate_job(self, job):
        checked = job_from_url(job.url, {})
        if (job.platform != self.platform_key or job.job_id != checked.job_id
                or job.external_job_id != checked.external_job_id):
            raise ValueError('Findy 职位身份不匹配')
        return checked.url

    def _stop(self, reason, stage=Stage.SOURCE_URL):
        raise GreenSearchDOMPending(stage, reason)

    def _check_navigation(self, stage):
        url = self.page.url
        if login_url(url, self.platform_key):
            self._stop('NEEDS_LOGIN', stage)
        if self._navigation_stage == Stage.SOURCE_NAVIGATION:
            if url != self._target:
                self._stop('SOURCE_URL_MISMATCH', stage)
        else:
            try:
                checked = job_from_url(url, {})
            except ValueError:
                self._stop('JOB_URL_MISMATCH', stage)
            if checked.job_id != job_from_url(self._target, {}).job_id:
                self._stop('JOB_URL_MISMATCH', stage)

    def _read(self, script, stage):
        self._check_navigation(stage)
        try:
            data = self.page.evaluate(script)
        except Exception:
            self._check_navigation(stage)
            raise
        self._check_navigation(stage)
        return data

    def _snapshot(self, stage):
        snapshot = self._read(SNAPSHOT, stage)
        if not isinstance(snapshot, dict):
            self._stop('PARSE_ERROR', stage)
        if snapshot.get('login'):
            self._stop('NEEDS_LOGIN', stage)
        if snapshot.get('ready') != 'complete' or snapshot.get('busy'):
            self._stop('PARSE_ERROR', stage)
        return snapshot

    def _navigate(self, target, stage):
        self._target, self._navigation_stage = target, stage
        try:
            self.page.goto(target, wait_until='load', timeout=15000)
        except Exception:
            if login_url(self.page.url, self.platform_key):
                self._stop('NEEDS_LOGIN', stage)
            raise
        self._check_navigation(stage)

    def search_cards(self, keyword, page):
        target = self.source_url(keyword, page)
        self._navigate(target, Stage.SOURCE_NAVIGATION)
        # Only local waits on the same page: at most 2 seconds for late cards.
        for attempt in range(5):
            if attempt:
                self.page.wait_for_timeout(500)
            snapshot = self._snapshot(Stage.SOURCE_URL)
            if self.page.url != target:
                self._stop('SOURCE_URL_MISMATCH')
            jobs = {}
            links = snapshot.get('links')
            if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
                self._stop('PARSE_ERROR', Stage.JOB_LINKS)
            for link in links:
                try:
                    job = job_from_url(link, {})
                except ValueError:
                    continue
                jobs.setdefault(job.job_id, job)
            if jobs:
                return list(jobs.values())
        # Absence is not evidence of an empty result/end of pagination.
        self._stop('NO_VALID_JOB_LINKS', Stage.JOB_LINKS)

    def _capture_safe_detail_diagnostic(self):
        try:
            self.last_safe_detail_diagnostic = sanitize(
                self._read(STRUCTURE, Stage.DETAIL_NAVIGATION))
        except Exception:
            # Optional evidence must preserve the primary failure.
            pass

    def job_detail(self, job):
        self.last_safe_detail_diagnostic = None
        target = self.validate_job(job)
        self._navigate(target, Stage.DETAIL_NAVIGATION)
        snapshot = self._snapshot(Stage.DETAIL_NAVIGATION)
        try:
            redirected = job_from_url(self.page.url, {})
            canonical = job_from_url(snapshot.get('canonical'), {})
        except ValueError:
            self._stop('JOB_URL_MISMATCH', Stage.DETAIL_NAVIGATION)
        if (redirected.job_id != job.job_id or canonical.job_id != job.job_id
                or canonical.url != redirected.url):
            self._stop('JOB_URL_MISMATCH', Stage.DETAIL_NAVIGATION)
        # Same-page reads only: at most 2 extra seconds for detail rendering.
        for attempt in range(5):
            if attempt:
                self._check_navigation(Stage.DETAIL_NAVIGATION)
                self.page.wait_for_timeout(500)
                retry_snapshot = self._snapshot(Stage.DETAIL_NAVIGATION)
                try:
                    retry_canonical = job_from_url(retry_snapshot.get('canonical'), {})
                except ValueError:
                    self._stop('JOB_URL_MISMATCH', Stage.DETAIL_NAVIGATION)
                if retry_canonical.url != target:
                    self._stop('JOB_URL_MISMATCH', Stage.DETAIL_NAVIGATION)
            data = self._read(DETAIL, Stage.DETAIL_NAVIGATION)
            if (not isinstance(data, dict) or not isinstance(data.get('sections'), dict)
                    or not isinstance(data.get('title'), str)
                    or any(not isinstance(k, str) or not isinstance(v, str)
                           for k, v in data['sections'].items())):
                self._stop('PARSE_ERROR', Stage.DETAIL_RESPONSIBILITIES)
            # Recheck after reading; never persist content from an auth/foreign redirect.
            final_snapshot = self._snapshot(Stage.DETAIL_NAVIGATION)
            try:
                final_canonical = job_from_url(final_snapshot.get("canonical"), {})
            except ValueError:
                self._stop("JOB_URL_MISMATCH", Stage.DETAIL_NAVIGATION)
            if final_canonical.url != target:
                self._stop("JOB_URL_MISMATCH", Stage.DETAIL_NAVIGATION)
            if job_from_url(self.page.url, {}).job_id != job.job_id:
                self._stop('JOB_URL_MISMATCH', Stage.DETAIL_NAVIGATION)
            fields = parse_fields(data['sections'], data['title'])
            if fields.get('title') and fields.get('responsibilities'):
                return fields
        if not fields.get('title'):
            self._capture_safe_detail_diagnostic()
            self._stop('TITLE_MISSING', Stage.DETAIL_TITLE)
        if not fields.get('responsibilities'):
            self._capture_safe_detail_diagnostic()
            self._stop('RESPONSIBILITIES_MISSING', Stage.DETAIL_RESPONSIBILITIES)
        return fields
