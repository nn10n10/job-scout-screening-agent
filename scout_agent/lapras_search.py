"""Read-only broad Lapras search using observed routes and semantic labels."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from scout_agent.green_discovery import GreenSearchDOMPending, Stage
from scout_agent.platform_discovery import SNAPSHOT, login_url
from scout_agent.search_platforms import Job, job_identity

ORIGIN = 'https://lapras.com'
SOURCES = ('求人検索',)
JOB_PATH = re.compile(r'/jobs/([0-9]+)')
ALIASES = {
    '仕事内容': 'responsibilities', '業務内容': 'responsibilities',
    '職務内容': 'responsibilities',
    '応募資格': 'required', '必須要件': 'required', '必須スキル': 'required',
    '必須スキル/経験': 'required',
    '歓迎要件': 'preferred', '歓迎スキル': 'preferred', '歓迎スキル/経験': 'preferred',
    '開発環境': 'technology', '技術': 'technology', '給与': 'salary', '年収': 'salary',
    '勤務地': 'location', 'リモート': 'remote', 'リモートワーク': 'remote',
    '企業名': 'company', '会社名': 'company', '雇用形態': 'employment',
}


def job_from_url(url, fields):
    if not isinstance(url, str) or not url or re.search(r'[\s\x00-\x1f\x7f]', url):
        raise ValueError('Lapras 职位 URL 无效')
    parts = urlsplit(urljoin(ORIGIN, url))
    match = JOB_PATH.fullmatch(parts.path)
    if parts.scheme != 'https' or parts.netloc != 'lapras.com' or not match:
        raise ValueError('Lapras 职位 URL 不在白名单')
    external_id = match[1]
    return Job(job_identity('lapras', external_id), ORIGIN + parts.path.rstrip('/'),
               fields, platform='lapras', external_job_id=external_id)


def parse_fields(sections, title=''):
    fields = {'title': title.strip()} if title.strip() else {}
    for label, value in sections.items():
        key = ALIASES.get(label)
        if key and isinstance(value, str) and value.strip():
            fields[key] = value.strip()
    # Fixed priority among explicitly allowed duties labels.
    for label in ('業務内容', '仕事内容', '職務内容'):
        value = sections.get(label)
        if isinstance(value, str) and value.strip():
            fields['responsibilities'] = value.strip()
            break
    return fields


# Semantic HTML only: no site CSS selectors, body fallback, clicks or form submission.
DETAIL = r"""() => {
 const labels = LABELS_JSON;
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const headings = 'h1,h2,h3,h4,dt,th';
 const carriers = headings + ',label,[role=heading],p,span,div,strong';
 const text = e => (e.innerText || e.textContent || '').trim();
 const titleNode = Array.from(document.querySelectorAll('h1')).find(e => visible(e) && text(e));
 const cleanTitle = value => {
   let result = (value || '').trim();
   for (const suffix of [' | LAPRAS（ラプラス）', ' | LAPRAS']) {
     if (result === suffix.trim()) return '';
     if (result.endsWith(suffix)) {
       result = result.slice(0, -suffix.length).trim();
       break;
     }
   }
   return ['LAPRAS', 'LAPRAS（ラプラス）'].includes(result) ? '' : result;
 };
 const meta = Array.from(document.querySelectorAll('meta[property="og:title"]'));
 const title = titleNode ? text(titleNode) :
   cleanTitle(meta[0]?.getAttribute('content')) || cleanTitle(document.title);
 const sections = {};
 const labelOf = e => visible(e) && e.matches(carriers) && labels.includes(text(e)) ? text(e) : '';
 // Only subsequent siblings in the label's own container; nested labels stop
 // traversal before another section's contents can enter responsibilities.
 const collect = (node, label, texts) => {
   if (!visible(node)) return false;
   if (labelOf(node) || node.matches(headings) &&
       !(label === '仕事内容' && text(node) === '概要')) return true;
   if (node.children?.length) {
     for (const child of node.children) {
       if (collect(child, label, texts)) return true;
     }
   } else if (!(label === '仕事内容' && text(node) === '概要')) {
     texts.push(text(node));
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
   const value = texts.filter(Boolean).join('\n');
   if (value && !sections[label]) sections[label] = value;
 }
 return {title, sections};
}""".replace("LABELS_JSON", json.dumps(list(ALIASES), ensure_ascii=False))


class LaprasSearchAdapter:
    platform_key = 'lapras'
    source_labels = SOURCES
    supports_pagination = False
    max_verified_page = 1

    def ensure_verified(self):
        # Enabled by supervisor route/probe evidence; live parsing remains to be verified.
        pass

    def source_url(self, label, page=1):
        if label not in SOURCES or type(page) is not int or page != 1:
            raise ValueError('Lapras 搜索来源或页码无效')
        return ORIGIN + '/jobs/search'

    def validate_job(self, job):
        checked = job_from_url(job.url, {})
        if (job.platform != self.platform_key or job.job_id != checked.job_id
                or job.external_job_id != checked.external_job_id):
            raise ValueError('Lapras 职位身份不匹配')
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
        snapshot = self._snapshot(Stage.SOURCE_URL)
        if self.page.url != target:
            self._stop('SOURCE_URL_MISMATCH')
        jobs = {}
        self.ignored_slug_links = 0
        links = snapshot.get('links')
        if not isinstance(links, list):
            self._stop('PARSE_ERROR', Stage.JOB_LINKS)
        for link in links:
            try:
                job = job_from_url(link, {})
            except ValueError:
                if isinstance(link, str):
                    parts = urlsplit(urljoin(ORIGIN, link))
                    if (parts.scheme == "https" and parts.netloc == "lapras.com"
                            and re.fullmatch(r"/jobs/[A-Za-z0-9_-]+", parts.path)
                            and parts.path != "/jobs/search"):
                        self.ignored_slug_links += 1
                continue
            jobs.setdefault(job.job_id, job)
        if not jobs:
            # Absence is not evidence of an empty result/end of pagination.
            self._stop('NO_STABLE_JOB_LINKS', Stage.JOB_LINKS)
        return list(jobs.values())

    def job_detail(self, job):
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
        data = self._read(DETAIL, Stage.DETAIL_NAVIGATION)
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
        if (not isinstance(data, dict) or not isinstance(data.get('sections'), dict)
                or not isinstance(data.get('title'), str)):
            self._stop('PARSE_ERROR', Stage.DETAIL_RESPONSIBILITIES)
        fields = parse_fields(data['sections'], data['title'])
        if not fields.get('title'):
            self._stop('TITLE_MISSING', Stage.DETAIL_TITLE)
        if not fields.get('responsibilities'):
            self._stop('RESPONSIBILITIES_MISSING', Stage.DETAIL_RESPONSIBILITIES)
        return fields
