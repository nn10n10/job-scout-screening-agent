"""Read-only doda Search, restricted to supervisor-verified routes."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from scout_agent.green_discovery import Stage
from scout_agent.platform_discovery import TYPE_SNAPSHOT
from scout_agent.platforms.doda import _plain
from scout_agent.search_platforms import Job, job_identity
from scout_agent.type_search import TypeSearchAdapter

ORIGIN = 'https://doda.jp'
SOURCES = ('インフラエンジニア',)
SOURCE = ORIGIN + '/DodaFront/View/JobSearchList/j_oc__0314M/-preBtn__3/'
JOB_PATH = re.compile(r'/DodaFront/View/JobSearchDetail/j_jid__([0-9]+)/-tab__(pr|jd)/')
EXTENDED_PATH = re.compile(JOB_PATH.pattern + r'-[A-Za-z][A-Za-z0-9_-]*__[^/\s?#]+/')
SNAPSHOT = "() => { const s = (" + TYPE_SNAPSHOT + ")(); " + \
    "s.payload = document.querySelector('#__NEXT_DATA__')?.textContent ?? null; return s; }"


def route(url, *, extended=False, normalize_slashes=False):
    if not isinstance(url, str) or not url or re.search(r'[\s\x00-\x1f\x7f]', url):
        raise ValueError('doda 职位 URL 无效')
    parts = urlsplit(ORIGIN + url if url.startswith('/') and not url.startswith('//') else url)
    if parts.scheme != 'https' or parts.netloc != 'doda.jp' or '?' in url or '#' in url:
        raise ValueError('doda 职位 URL 不在白名单')
    path = re.sub(r'/+', '/', parts.path) if normalize_slashes else parts.path
    match = JOB_PATH.fullmatch(path) or (EXTENDED_PATH.fullmatch(path) if extended else None)
    if not match:
        raise ValueError('doda 职位 route 不在白名单')
    return match[1], match[2], ORIGIN + path


def job_from_url(url, fields):
    jid, _, checked = route(url)
    return Job(job_identity('doda', jid), checked, fields, platform='doda', external_job_id=jid)


def parse_fields(payload, expected_jid):
    node = payload
    for key in ('props', 'pageProps', 'job', 'job'):
        if not isinstance(node, dict):
            raise ValueError('doda NEXT_DATA 结构无效')
        node = node.get(key)
    if (not isinstance(node, dict) or type(node.get('jid')) not in (str, int)
            or str(node['jid']) != expected_jid or not isinstance(node.get('recruit'), dict)):
        raise ValueError('doda NEXT_DATA 身份或 recruit 无效')
    recruit = node['recruit']
    fields = {'company': _plain(node.get('corporateName')) or '',
              'title': _plain(node.get('occupationName')) or ''}
    for field, keys in {
        'responsibilities': ('jobContentOutline', 'jobContentDetail'),
        'required': ('targetMemberOutline', 'targetMemberDetail'),
        'technology': ('developmentEnvironment', 'projectCase'),
        'salary': ('salary',), 'location': ('jobState', 'access'),
    }.items():
        fields[field] = '\n'.join(text for key in keys if (text := _plain(recruit.get(key))))
    fields.update(preferred='', remote='')
    return fields


class DodaSearchAdapter(TypeSearchAdapter):
    platform_key = 'doda'
    source_labels = SOURCES
    supports_pagination = True
    max_verified_page = None

    def source_url(self, label, page=1):
        if label not in SOURCES or type(page) is not int or page < 1:
            raise ValueError('doda 搜索来源或页码无效')
        return SOURCE + (f'-page__{page}/' if page >= 2 else '')

    def validate_job(self, job):
        checked = job_from_url(job.url, {})
        if (job.platform != 'doda' or job.job_id != checked.job_id
                or job.external_job_id != checked.external_job_id):
            raise ValueError('doda 职位身份不匹配')
        return checked.url

    def _check_navigation(self, stage):
        from scout_agent.platform_discovery import login_url
        if login_url(self.page.url, 'doda'):
            self._stop('NEEDS_LOGIN', stage)
        if self._navigation_stage == Stage.SOURCE_NAVIGATION:
            if self.page.url != self._target:
                self._stop('SOURCE_URL_MISMATCH', stage)
        else:
            try:
                jid, tab, _ = route(self.page.url, extended=True)
                expected_jid, expected_tab, _ = route(self._target)
            except ValueError:
                self._stop('JOB_URL_MISMATCH', stage)
            if (jid, tab) != (expected_jid, expected_tab):
                self._stop('JOB_URL_MISMATCH', stage)

    def _state(self, detail=False):
        stage = Stage.DETAIL_NAVIGATION if detail else Stage.SOURCE_URL
        data = self._read(SNAPSHOT if detail else TYPE_SNAPSHOT, stage)
        if not isinstance(data, dict):
            self._stop('PARSE_ERROR', stage)
        if data.get('login'):
            self._stop('NEEDS_LOGIN', stage)
        if detail:
            try:
                jid, _, _ = route(data.get('canonical'))
            except ValueError:
                self._stop('JOB_URL_MISMATCH', stage)
            if jid != route(self._target)[0]:
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
                    job = job_from_url(link, {})
                except ValueError:
                    continue
                if job.job_id not in jobs or route(job.url)[1] == 'jd':
                    jobs[job.job_id] = job
            if jobs:
                return list(jobs.values())
        self._stop('NO_VALID_JOB_LINKS', Stage.JOB_LINKS)

    def _payload_fields(self, data, jid):
        if data.get('payload') is None:
            return None
        try:
            payload = json.loads(data['payload'])
            return parse_fields(payload, jid)
        except (ValueError, TypeError):
            self._stop('PARSE_ERROR', Stage.DETAIL_NAVIGATION)

    def job_detail(self, job):
        self._navigate(self.validate_job(job), Stage.DETAIL_NAVIGATION)
        fields = {}
        for navigation in range(2):
            for attempt in range(5):
                if attempt:
                    self.page.wait_for_timeout(500)
                data = self._state(detail=True)
                parsed = self._payload_fields(data, job.external_job_id)
                # A separate post-read proves canonical and payload identity still agree.
                final = self._state(detail=True)
                final_fields = self._payload_fields(final, job.external_job_id)
                if (data.get('ready') != 'complete' or data.get('busy')
                        or final.get('ready') != 'complete' or final.get('busy')
                        or parsed is None or final_fields is None):
                    continue
                fields = final_fields
                if fields.get('title') and fields.get('responsibilities'):
                    return fields
            if (final.get('ready') != 'complete' or final.get('busy') or final_fields is None):
                self._stop('PARSE_ERROR', Stage.DETAIL_NAVIGATION)
            if navigation or route(self.page.url, extended=True)[1] != 'pr' or fields.get('responsibilities'):
                break
            links = final.get('links')
            if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
                self._stop('PARSE_ERROR', Stage.DETAIL_NAVIGATION)
            fallback = None
            for link in links:
                try:
                    jid, tab, url = route(link, normalize_slashes=True)
                except ValueError:
                    continue
                if jid == job.external_job_id and tab == 'jd':
                    fallback = url
                    break
            if not fallback:
                break
            self._navigate(fallback, Stage.DETAIL_NAVIGATION)
        if not fields.get('title'):
            self._stop('TITLE_MISSING', Stage.DETAIL_TITLE)
        self._stop('RESPONSIBILITIES_MISSING', Stage.DETAIL_RESPONSIBILITIES)
