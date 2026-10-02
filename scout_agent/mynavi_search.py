"""Read-only Active Search using supervisor-verified Mynavi routes and DOM."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from scout_agent.green_discovery import Stage
from scout_agent.platform_discovery import TYPE_SNAPSHOT, login_url
from scout_agent.search_platforms import FIELDS, Job, job_identity
from scout_agent.type_search import TypeSearchAdapter

ORIGIN = 'https://tenshoku.mynavi.jp'
SOURCES = ('インフラエンジニア',)
SOURCE = ORIGIN + '/engineer/list/o166/'
JOB_PATH = re.compile(r'/jobinfo-([0-9]+-[0-9]+-[0-9]+-[0-9]+)/')


def job_from_url(url, fields, *, tracking=False):
    if not isinstance(url, str) or not url or re.search(r'[\s\x00-\x1f\x7f]', url):
        raise ValueError('マイナビ 职位 URL 无效')
    parts = urlsplit(url)
    match = JOB_PATH.fullmatch(parts.path)
    if (parts.scheme != 'https' or parts.netloc != 'tenshoku.mynavi.jp' or not match
            or (not tracking and ('?' in url or '#' in url))):
        raise ValueError('マイナビ 职位 URL 不在白名单')
    jid = match[1]
    return Job(job_identity('mynavi', jid), ORIGIN + parts.path, fields,
               platform='mynavi', external_job_id=jid)


DETAIL = r"""() => {
 const text = e => e ? (e.innerText || e.textContent || '').trim() : '';
 const local = selector => text(document.querySelector(selector));
 const description = document.querySelector('.jobPointArea__wrap-jobDescription');
 const duties = description ? Array.from(description.querySelectorAll(
   '.jobPointArea__head, .jobPointArea__body:not(.jobPointArea__body--large)'))
   .filter(e => !e.closest('.jobPointArea__body--large')).map(text).filter(Boolean) : [];
 const fields = {company: local('h1 .companyName'), title: local('h1 .occName'),
   responsibilities: duties.join('\n'), required: local('.jobPointArea__body--large'),
   salary: '', location: '', preferred: '', technology: '', remote: ''};
 for (const row of document.querySelectorAll('table.jobOfferTable tr')) {
   const label = text(row.querySelector('th'));
   if (label === '給与') fields.salary = text(row.querySelector('td'));
   if (label === '勤務地') fields.location = text(row.querySelector('td'));
 }
 return fields;
}"""


class MynaviSearchAdapter(TypeSearchAdapter):
    platform_key = 'mynavi'
    source_labels = SOURCES
    supports_pagination = True
    max_verified_page = None

    def source_url(self, label, page=1):
        if label not in SOURCES or type(page) is not int or page < 1:
            raise ValueError('マイナビ 搜索来源或页码无效')
        return SOURCE + (f'pg{page}/' if page >= 2 else '')

    def validate_job(self, job):
        checked = job_from_url(job.url, {})
        if (job.platform != self.platform_key or job.job_id != checked.job_id
                or job.external_job_id != checked.external_job_id):
            raise ValueError('マイナビ 职位身份不匹配')
        return checked.url

    def _check_navigation(self, stage):
        if login_url(self.page.url, self.platform_key):
            self._stop('NEEDS_LOGIN', stage)
        if self._navigation_stage == Stage.SOURCE_NAVIGATION:
            if self.page.url != self._target:
                self._stop('SOURCE_URL_MISMATCH', stage)
        else:
            try:
                current = job_from_url(self.page.url, {}, tracking=True)
                target = job_from_url(self._target, {})
            except ValueError:
                self._stop('JOB_URL_MISMATCH', stage)
            if current.job_id != target.job_id:
                self._stop('JOB_URL_MISMATCH', stage)

    def _state(self, detail=False):
        stage = Stage.DETAIL_NAVIGATION if detail else Stage.SOURCE_URL
        data = self._read(TYPE_SNAPSHOT, stage)
        if not isinstance(data, dict):
            self._stop('PARSE_ERROR', stage)
        if data.get('login'):
            self._stop('NEEDS_LOGIN', stage)
        if detail:
            try:
                canonical = job_from_url(data.get('canonical'), {}, tracking=True)
            except ValueError:
                self._stop('JOB_URL_MISMATCH', stage)
            if canonical.job_id != job_from_url(self._target, {}).job_id:
                self._stop('JOB_URL_MISMATCH', stage)
        return data

    def search_cards(self, keyword, page):
        self._navigate(self.source_url(keyword, page), Stage.SOURCE_NAVIGATION)
        for attempt in range(5):
            if attempt:
                self.page.wait_for_timeout(500)
            data = self._state()
            if data.get('ready') != 'complete' or data.get('busy'):
                continue
            links = data.get('links')
            if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
                self._stop('PARSE_ERROR', Stage.JOB_LINKS)
            jobs = {}
            for link in links:
                try:
                    job = job_from_url(link, {}, tracking=True)
                except ValueError:
                    continue
                jobs.setdefault(job.job_id, job)
            if jobs:
                return list(jobs.values())
        self._stop('NO_VALID_JOB_LINKS', Stage.JOB_LINKS)

    def job_detail(self, job):
        self._navigate(self.validate_job(job), Stage.DETAIL_NAVIGATION)
        fields = {}
        after = {}
        for attempt in range(5):
            if attempt:
                self.page.wait_for_timeout(500)
            before = self._state(detail=True)
            if before.get('ready') != 'complete' or before.get('busy'):
                continue
            fields = self._read(DETAIL, Stage.DETAIL_NAVIGATION)
            after = self._state(detail=True)
            if (not isinstance(fields, dict) or set(fields) != set(FIELDS)
                    or any(not isinstance(value, str) for value in fields.values())):
                self._stop('PARSE_ERROR', Stage.DETAIL_NAVIGATION)
            if after.get('ready') != 'complete' or after.get('busy'):
                continue
            fields = {key: value.strip() for key, value in fields.items()}
            if fields['title'] and fields['responsibilities']:
                return fields
        if before.get('ready') != 'complete' or before.get('busy') or after.get('ready') != 'complete' or after.get('busy'):
            self._stop('PARSE_ERROR', Stage.DETAIL_NAVIGATION)
        if not fields.get('title'):
            self._stop('TITLE_MISSING', Stage.DETAIL_TITLE)
        self._stop('RESPONSIBILITIES_MISSING', Stage.DETAIL_RESPONSIBILITIES)
