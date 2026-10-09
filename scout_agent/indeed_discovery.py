"""Stage A only: fixed, redacted Indeed Japan discovery evidence."""
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

QUERY_KEYS = ('q', 'l', 'start', 'sort', 'from', 'rq', 'rsIdx')
SELECTORS = ('#jobDescriptionText', 'h1.jobsearch-JobInfoHeader-title',
             '[data-testid="inlineHeader-companyName"]', '[data-testid="job-location"]')
JOB_KEYS = ('title', 'description', 'hiringOrganization', 'jobLocation', 'baseSalary',
            'employmentType', 'datePosted', 'validThrough')
KINDS = ('viewjob', 'rc-clk', 'pagead-clk', 'other-jk-path')


def same_origin(url):
    from scout_agent.platform_discovery import allowed
    return allowed(url, 'indeed')


def identity(url):
    if not same_origin(url):
        return None
    values = [v for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True) if k == 'jk']
    return values[0] if len(values) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{8,64}', values[0]) else None


def detail(url):
    return identity(url) if same_origin(url) and urlsplit(url).path == '/viewjob' else None


def source(url):
    path = unquote(urlsplit(url).path)
    return ('jobs-query' if path == '/jobs' else 'seo-search'
            if re.fullmatch(r'/q-.+-l-.+-求人\.html', path) else 'other')


def challenge_url(url):
    return same_origin(url) and bool(re.search(
        r'/(?:captcha|challenge|cdn-cgi/challenge-platform)(?:/|$)', urlsplit(url).path, re.I))


def validate(snapshot):
    from scout_agent.platform_discovery import validate_findy_snapshot
    if not isinstance(snapshot, dict):
        raise ValueError('Invalid snapshot')
    if not {'links', 'pagination', 'canonical'} <= snapshot.keys():
        raise ValueError('Incomplete snapshot')
    validate_findy_snapshot(snapshot)
    for key in ('login', 'challenge', 'busy'):
        if type(snapshot.get(key)) is not bool:
            raise ValueError('Invalid state')
    if snapshot.get('ready') not in ('loading', 'interactive', 'complete'):
        raise ValueError('Invalid ready state')
    for field, keys in (('selectors', SELECTORS), ('job_posting_keys', JOB_KEYS)):
        values = snapshot.get(field)
        if not isinstance(values, dict) or set(values) != set(keys) or any(type(v) is not bool for v in values.values()):
            raise ValueError('Invalid structure')
    if type(snapshot.get('job_posting_present')) is not bool:
        raise ValueError('Invalid structured state')


def evidence(url, snapshot):
    from scout_agent.platform_discovery import failure, login_url
    if challenge_url(url):
        return failure('indeed', 'CHALLENGE')
    if login_url(url, 'indeed'):
        return failure('indeed', 'NEEDS_LOGIN')
    if not same_origin(url):
        return failure('indeed', 'DOMAIN_BLOCKED')
    try:
        validate(snapshot)
    except (ValueError, TypeError):
        return failure('indeed', 'READ_FAILED')
    if snapshot['challenge']:
        return failure('indeed', 'CHALLENGE')
    if snapshot['login']:
        return failure('indeed', 'NEEDS_LOGIN')
    if snapshot['ready'] != 'complete' or snapshot['busy']:
        return failure('indeed', 'LOADING')
    current = detail(url)
    route = source(url)
    counts = dict.fromkeys(KINDS, 0)
    for link in snapshot.get('links', [])[:2000]:
        if identity(link):
            kind = {'/viewjob': 'viewjob', '/rc/clk': 'rc-clk', '/pagead/clk': 'pagead-clk'}.get(urlsplit(link).path, 'other-jk-path')
            counts[kind] += 1
    pagination = [item for item in snapshot.get('pagination', [])[:2000]
                  if item['kind'] in {'next', 'prev', 'page-number'}
                  and same_origin(item['url']) and source(item['url']) != 'other'
                  and 'start' in dict(parse_qsl(urlsplit(item['url']).query, keep_blank_values=True))]
    keys = {k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    same = bool(current and current == detail(snapshot.get('canonical')))
    result = {
        'platform': 'indeed', 'safe_failure_category': 'NONE' if current or route != 'other' else 'NO_JOB_LINK_EVIDENCE',
        'source_route': route,
        'route_path': '/viewjob' if current else '/jobs' if route == 'jobs-query' else '/q-:query-l-:location-求人.html' if route == 'seo-search' else 'other',
        'page_kind': 'detail' if current else 'list' if route != 'other' else 'other',
        'query_keys_present': {key: key in keys for key in QUERY_KEYS},
        'other_query_key_present': bool(keys - set(QUERY_KEYS)),
        'jk_link_count': sum(counts.values()), 'jk_link_kind_counts': counts,
        'jk_link_kinds': [kind for kind in KINDS if counts[kind]],
        'stable_identity_candidate': 'jk' if same else None,
        'canonical_pattern': '/viewjob?jk=:id' if same else None,
        'start_parameter_present': 'start' in keys or bool(pagination),
        'pagination_mode': 'start-offset-candidate' if pagination else 'unknown',
        'pagination_candidates': sorted({item['kind'] for item in pagination}),
    }
    if current:
        result.update(detail_selectors_present={k: snapshot['selectors'][k] for k in SELECTORS},
                      job_posting_present=snapshot['job_posting_present'],
                      job_posting_keys_present={k: snapshot['job_posting_present'] and snapshot['job_posting_keys'][k] for k in JOB_KEYS})
    return result


# Only fixed booleans leave structured DOM reads. URLs are compared in memory,
# then discarded by evidence(); no text, JD or JSON-LD payload is returned.
SNAPSHOT = r'''() => {
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const challenge = /(?:^|\/)(?:captcha|challenge)(?:\/|$)/i.test(location.pathname) ||
   /^(?:Just a moment(?:\.\.\.)?|Additional Verification Required|Access Denied|Verify you are human|ロボットではないことを確認してください)$/i.test(document.title.trim()) ||
   Array.from(document.querySelectorAll('iframe[src],form[action]')).filter(visible).some(e =>
     /captcha|challenge|challenges\.cloudflare\.com/i.test(e.getAttribute('src') || e.getAttribute('action') || '')) ||
   !!document.querySelector('#challenge-running,#challenge-form,.g-recaptcha,.h-captcha,[data-sitekey]');
 const login = Array.from(document.querySelectorAll('input[type="password"],form[action],h1,h2,button,[role="button"]')).filter(visible).some(e =>
   e.matches('input[type="password"]') || /(?:^|\/)(?:login|signin|sign-in|sign_in)(?:\/|$|\?)/i.test(e.getAttribute('action') || '') ||
   /^(?:ログイン|サインイン|Sign in|Log in|Session expired)$/i.test((e.textContent || '').trim()));
 const selectors = Object.fromEntries(SELECTORS.map(s => [s, false]));
 const jobKeys = Object.fromEntries(JOB_KEYS.map(k => [k, false]));
 const base = {challenge, login, ready: document.readyState, busy: !!document.querySelector('[aria-busy="true"]'),
   selectors, job_posting_present: false, job_posting_keys: jobKeys, links: [], pagination: [], canonical: null};
 if (challenge || login || base.ready !== 'complete' || base.busy) return base;
 base.canonical = document.querySelector('link[rel="canonical"]')?.href || null;
 const anchors = Array.from(document.querySelectorAll('a[href]')).filter(visible).filter(e => {
   try { return new URL(e.href).origin === 'https://jp.indeed.com'; } catch (_) { return false; }
 }).slice(0, 2000);
 base.links = anchors.map(e => e.href);
 base.pagination = anchors.map(e => {
   const rel = (e.getAttribute('rel') || '').split(/\s+/);
   const label = (e.getAttribute('aria-label') || e.textContent || '').trim();
   const kind = rel.includes('next') || /^(?:次へ|次のページ|Next|Next Page|›|»)$/i.test(label) ? 'next' :
     rel.includes('prev') || /^(?:前へ|前のページ|Previous|Previous Page|Prev|‹|«)$/i.test(label) ? 'prev' : /^\d+$/.test(label) ? 'page-number' : null;
   return kind ? {url: e.href, kind} : null;
 }).filter(Boolean);
 const current = new URL(location.href);
 const ids = current.searchParams.getAll('jk');
 if (current.pathname !== '/viewjob' || ids.length !== 1 || !/^[A-Za-z0-9_-]{8,64}$/.test(ids[0])) return base;
 SELECTORS.forEach(s => { selectors[s] = !!document.querySelector(s); });
 const postings = [];
 const visit = v => {
   if (Array.isArray(v)) { v.forEach(visit); return; }
   if (!v || typeof v !== 'object') return;
   if (v['@type'] === 'JobPosting' || (Array.isArray(v['@type']) && v['@type'].includes('JobPosting'))) postings.push(v);
   if (v['@graph']) visit(v['@graph']);
 };
 try {
   document.querySelectorAll('script[type="application/ld+json"]').forEach(e => visit(JSON.parse(e.textContent || '')));
   base.job_posting_present = postings.length > 0;
   JOB_KEYS.forEach(k => { jobKeys[k] = postings.some(p => Object.prototype.hasOwnProperty.call(p, k)); });
 } catch (_) { base.job_posting_present = false; JOB_KEYS.forEach(k => { jobKeys[k] = false; }); }
 return base;
}'''.replace('SELECTORS', json.dumps(SELECTORS)).replace('JOB_KEYS', json.dumps(JOB_KEYS))
