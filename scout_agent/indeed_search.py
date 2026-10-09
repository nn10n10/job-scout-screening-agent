"""Read-only Indeed Japan search, limited to verified first-page sources."""
from urllib.parse import parse_qsl, urlencode, urlsplit

from scout_agent.indeed_discovery import SNAPSHOT, challenge_url, detail, identity, same_origin, validate
from scout_agent.green_discovery import GreenSearchDOMPending, Stage
from scout_agent.platform_discovery import login_url
from scout_agent.search_platforms import FIELDS, Job, job_identity

ORIGIN = 'https://jp.indeed.com'
SOURCES = ('インフラエンジニア', 'クラウドエンジニア', 'SRE', 'DevOps')


def job_from_url(url, fields):
    jk = detail(url)
    if not jk or url != ORIGIN + '/viewjob?' + urlencode({'jk': jk}):
        raise ValueError('Indeed 职位 URL 无效')
    return Job(job_identity('indeed', jk), url, fields, platform='indeed', external_job_id=jk)


DETAIL = r'''() => {
 const str = v => typeof v === 'string' ? v.trim() : '';
 const scalar = v => typeof v === 'string' || (typeof v === 'number' && Number.isFinite(v)) ? String(v).trim() : '';
 const obj = v => v && typeof v === 'object' && !Array.isArray(v);
 const local = selector => {
   const e = document.querySelector(selector);
   return e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' ? str(e.innerText || e.textContent) : '';
 };
 const postings = [];
 const visit = v => {
   if (Array.isArray(v)) { v.forEach(visit); return; }
   if (!obj(v)) return;
   if (v['@type'] === 'JobPosting' || (Array.isArray(v['@type']) && v['@type'].includes('JobPosting'))) postings.push(v);
   if (v['@graph']) visit(v['@graph']);
 };
 for (const e of document.querySelectorAll('script[type="application/ld+json"]')) {
   try { visit(JSON.parse(e.textContent || '')); } catch (_) { /* fixed DOM fallback */ }
 }
 // Multiple postings cannot safely be attributed to this job.
 const p = postings.length === 1 ? postings[0] : {};
 const plain = html => {
   if (!str(html)) return '';
   const doc = new DOMParser().parseFromString(html, 'text/html');
   doc.querySelectorAll('script,style,template').forEach(e => e.remove());
   doc.querySelectorAll('br,p,div,li,h1,h2,h3').forEach(e => e.appendChild(doc.createTextNode('\n')));
   return str(doc.body.textContent);
 };
 const salary = v => {
   if (!obj(v)) return '';
   const amount = obj(v.value) ? v.value : v;
   const number = scalar(amount.value) || [scalar(amount.minValue), scalar(amount.maxValue)].filter(Boolean).join('–');
   if (!number) return '';
   return [scalar(v.currency), number, scalar(amount.unitText)].filter(Boolean).join(' ');
 };
 const location = v => {
   const places = Array.isArray(v) ? v : [v];
   return places.map(place => {
     if (!obj(place) || !obj(place.address)) return '';
     const a = place.address;
     const country = obj(a.addressCountry) ? scalar(a.addressCountry.name) : scalar(a.addressCountry);
     return [country, scalar(a.addressRegion), scalar(a.addressLocality), scalar(a.streetAddress), scalar(a.postalCode)].filter(Boolean).join(' ');
   }).filter(Boolean).join('; ');
 };
 return {
   company: (obj(p.hiringOrganization) ? str(p.hiringOrganization.name) : '') || local('[data-testid="inlineHeader-companyName"]'),
   title: str(p.title) || local('h1.jobsearch-JobInfoHeader-title'),
   responsibilities: plain(p.description) || local('#jobDescriptionText'),
   salary: salary(p.baseSalary), location: location(p.jobLocation),
   required: '', preferred: '', technology: '', remote: ''
 };
}'''


class IndeedSearchAdapter:
    platform_key = 'indeed'
    source_labels = SOURCES
    supports_pagination = False
    max_verified_page = 1

    def ensure_verified(self):
        pass

    def source_url(self, label, page=1):
        if label not in SOURCES or type(page) is not int or page != 1:
            raise ValueError('Indeed 仅支持已验证关键词第一页')
        return ORIGIN + '/jobs?' + urlencode({'q': label})

    def validate_job(self, job):
        checked = job_from_url(job.url, {})
        if (job.platform, job.job_id, job.external_job_id) != ('indeed', checked.job_id, checked.external_job_id):
            raise ValueError('Indeed 职位身份不匹配')
        return checked.url

    def _stop(self, reason, stage):
        raise GreenSearchDOMPending(stage, reason)

    def _check(self, stage):
        url = self.page.url
        if challenge_url(url):
            self._stop('CHALLENGE', stage)
        if login_url(url, 'indeed'):
            self._stop('NEEDS_LOGIN', stage)
        if self._source is not None:
            if not same_origin(url) or urlsplit(url).path != '/jobs' or urlsplit(url).fragment:
                self._stop('SOURCE_URL_MISMATCH', stage)
            pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
            queries = [v for k, v in pairs if k == 'q']
            starts = [v for k, v in pairs if k == 'start']
            if queries != [self._source] or len(starts) > 1 or any(v != '0' for v in starts):
                self._stop('SOURCE_URL_MISMATCH', stage)
        elif detail(url) != self._jk:
            self._stop('JOB_URL_MISMATCH', stage)

    def _read(self, script, stage):
        self._check(stage)
        try:
            data = self.page.evaluate(script)
        except Exception:
            self._check(stage)
            self._stop('PARSE_ERROR', stage)
        self._check(stage)
        return data

    def _snapshot(self, stage):
        data = self._read(SNAPSHOT, stage)
        if isinstance(data, dict) and data.get('challenge') is True:
            self._stop('CHALLENGE', stage)
        if isinstance(data, dict) and data.get('login') is True:
            self._stop('NEEDS_LOGIN', stage)
        try:
            validate(data)
        except (ValueError, TypeError):
            self._stop('PARSE_ERROR', stage)
        if data['ready'] != 'complete' or data['busy']:
            self._stop('PARSE_ERROR', stage)
        if self._source is None and detail(data['canonical']) != self._jk:
            self._stop('JOB_URL_MISMATCH', stage)
        return data

    def _navigate(self, url, stage):
        try:
            self.page.goto(url, wait_until='load', timeout=15000)
        except Exception:
            self._check(stage)
            self._stop('PARSE_ERROR', stage)
        self._check(stage)

    def search_cards(self, keyword, page):
        target = self.source_url(keyword, page)
        self._source, self._jk = keyword, None
        self._navigate(target, Stage.SOURCE_NAVIGATION)
        snapshot = self._snapshot(Stage.SOURCE_URL)
        jobs = {}
        for link in snapshot['links']:
            jk = identity(link)
            if jk:
                job = job_from_url(ORIGIN + '/viewjob?' + urlencode({'jk': jk}), {})
                jobs.setdefault(job.job_id, job)
        if not jobs:
            self._stop('NO_VALID_JOB_LINKS', Stage.JOB_LINKS)
        return list(jobs.values())

    def job_detail(self, job):
        target = self.validate_job(job)
        self._source, self._jk = None, job.external_job_id
        stage = Stage.DETAIL_NAVIGATION
        self._navigate(target, stage)
        self._snapshot(stage)
        fields = self._read(DETAIL, stage)
        self._snapshot(stage)
        if not isinstance(fields, dict) or set(fields) != set(FIELDS) or any(not isinstance(v, str) for v in fields.values()):
            self._stop('PARSE_ERROR', stage)
        fields = {k: v.strip() for k, v in fields.items()}
        if not fields['title']:
            self._stop('TITLE_MISSING', Stage.DETAIL_TITLE)
        if not fields['responsibilities']:
            self._stop('RESPONSIBILITIES_MISSING', Stage.DETAIL_RESPONSIBILITIES)
        return fields
