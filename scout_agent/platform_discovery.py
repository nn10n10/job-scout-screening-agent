"""Manual, model-free discovery of already open authenticated tabs."""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlsplit, parse_qsl

from scout_agent import mynavi_discovery

from scout_agent.lapras_structure import STRUCTURE, sanitize
from scout_agent.doda_structure import STRUCTURE as DODA_STRUCTURE, sanitize as sanitize_doda
from scout_agent.findy_structure import STRUCTURE as FINDY_STRUCTURE, sanitize as sanitize_findy, exact_detail

PLATFORMS = {
    'forkwell': frozenset({'jobs.forkwell.com', 'forkwell.com'}),
    'findy': frozenset({'findy-code.io'}),
    'lapras': frozenset({'lapras.com'}),
    'type': frozenset({'type.jp'}),
    'doda': frozenset({'doda.jp'}),
    'mynavi': frozenset({'tenshoku.mynavi.jp'}),
}
SEGMENTS = frozenset({'jobs', 'job', 'search', '求人', 'companies', 'company',
                      'home', 'career', 'careers', 'list', 'detail', 'index', 'view'})
LABELS = frozenset({'仕事内容', '応募資格', '必須要件', '歓迎要件', '給与', '勤務地',
                    '勤務時間', '雇用形態', '福利厚生', '開発環境', '技術', '検索結果',
                    '求人検索', '募集要項', 'リモート', '年収'})
PAGINATION_KEYS = frozenset({'page', 'p', 'cursor', 'offset'})
# Only fixed labels/anchor markers leave the DOM; no body, profile or storage read.
SNAPSHOT = """() => {
 const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
 const login = Array.from(document.querySelectorAll('input[type="password"],form[action],h1,h2,button,[role="button"]'))
   .filter(visible).some(e => e.matches('input[type="password"]') ||
     /(?:^|\\/)(?:login|signin|sign-in|sign_in)(?:\\/|$|\\?)/i.test(e.getAttribute('action') || '') ||
     /^(?:ログイン|サインイン|登录|Sign in|Log in|Googleでログイン|Googleでサインイン|Sign in with Google|Session expired|セッションの有効期限が切れました|登录已失效)$/i.test((e.textContent || '').trim()));
 if (login) return {login: true};
 const labels = %s;
 return {
   canonical: document.querySelector('link[rel="canonical"]')?.href || null,
   controls: Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible)
     .map(e => (e.textContent || '').trim())
     .filter(t => /^(?:もっと見る|さらに表示|もっと読み込む|Load more|Show more)$/i.test(t))
     .map(() => 'load-more'),
   pagination: Array.from(document.querySelectorAll('a[href]')).filter(visible).slice(0, 2000)
     .map(e => {
       const rel = (e.getAttribute('rel') || '').split(/\\s+/);
       const label = (e.textContent || '').trim();
       const kind = rel.includes('next') ? 'next' : rel.includes('prev') ? 'prev' :
         /^(?:次へ|次のページ|Next|›|»)$/.test(label) ? 'next' :
         /^(?:前へ|前のページ|Previous|Prev|‹|«)$/.test(label) ? 'prev' :
         /^(?:もっと見る|さらに表示|もっと読み込む|Load more|Show more)$/i.test(label) ? 'load-more' :
         /^\\d+$/.test(label) ? 'page-number' : null;
       return kind ? {url: e.href, kind} : null;
     }).filter(Boolean),
   links: Array.from(document.querySelectorAll('a[href]')).filter(visible).slice(0, 2000).map(e => e.href),
   labels: Array.from(document.querySelectorAll('h1,h2,h3,h4,dt,label,th')).filter(visible)
     .map(e => (e.textContent || '').trim()).filter(t => labels.includes(t)),
   ready: document.readyState,
   busy: !!document.querySelector('[aria-busy="true"]'),
   spa: !!document.querySelector('#__next,#__nuxt,[data-reactroot]'),
   login: false
 };
}""" % json.dumps(sorted(LABELS), ensure_ascii=False)


# Read all visible Type anchors; other platforms retain their existing limits.
TYPE_SNAPSHOT = SNAPSHOT.replace(".slice(0, 2000)", "")


LAPRAS_DETAIL_SNAPSHOT = "() => { const snapshot = (" + SNAPSHOT + ")(); " + \
    "if (!snapshot.login) snapshot.lapras_structure = (" + STRUCTURE + ")(); return snapshot; }"


FINDY_DETAIL_SNAPSHOT = "() => { const snapshot = (" + SNAPSHOT + ")(); " + \
    "if (!snapshot.login) snapshot.findy_structure = (" + FINDY_STRUCTURE + ")(); return snapshot; }"

DODA_DETAIL_SNAPSHOT = "() => { const snapshot = (" + SNAPSHOT + ")(); " + \
    "if (!snapshot.login) { try { snapshot.doda_detail_structure = (" + DODA_STRUCTURE + \
    ")(); } catch (_) {} } return snapshot; }"


MYNAVI_SNAPSHOT = "() => { const snapshot = (" + SNAPSHOT + ")(); " + \
    "if (!snapshot.login) snapshot.mynavi_detail_structure = Object.fromEntries(" + \
    json.dumps(mynavi_discovery.SELECTORS) + \
    ".map(s => [s, !!document.querySelector(s)])); return snapshot; }"


def lapras_numeric_detail(platform, url):
    return (platform == 'lapras' and allowed(url, platform)
            and re.fullmatch(r'/jobs/[0-9]+/?', urlsplit(url).path) is not None)


def allowed(url, platform):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.netloc in PLATFORMS[platform]
                and not re.search(r'[\s\x00-\x1f\x7f]', url))
    except (ValueError, TypeError):
        return False


def path_pattern(url, *, platform=None):
    """Never retain arbitrary literal path segments, query values or fragments."""
    p = urlsplit(url)
    parts = p.path.split('/')
    return '/'.join(s if s in SEGMENTS or (platform == 'findy' and s == 'recommends') else ':id' if re.fullmatch(r'\d+', s)
                    else ':segment' if s else '' for s in parts)


def login_url(url, platform):
    """Recognize authentication routes without retaining OAuth parameters."""
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password:
            return False
        if p.netloc == 'accounts.google.com':
            return True
        return allowed(url, platform) and bool(re.search(
            r'/(?:login|signin|sign-in|sign_in|log-in)(?:/|$)', p.path, re.I))
    except (ValueError, TypeError):
        return False


def failure(platform, category):
    return {'platform': platform, 'safe_failure_category': category}


def platform_summary(rows, platforms):
    """One safe status per platform, with authentication taking precedence."""
    output = []
    for platform in platforms:
        categories = {r['safe_failure_category'] for r in rows if r['platform'] == platform}
        status = ('NEEDS_LOGIN' if 'NEEDS_LOGIN' in categories else
                  'BLOCKED' if categories - {'NONE', 'NO_OPEN_TAB'} else
                  'OK' if 'NONE' in categories else 'UNSUPPORTED')
        row = {'platform': platform, 'status': status}
        if status == 'NEEDS_LOGIN':
            row['message'] = f'[NEEDS_LOGIN] platform={platform}：登录已失效，请在 agent 专用浏览器中手动完成登录（如 Google 登录）后重试。'
        elif status == 'UNSUPPORTED':
            row['message'] = '没有可归属的平台标签页，尚无调查证据；不代表平台不支持 Search。'
        output.append(row)
    return output


def evidence(platform, url, snapshot):
    result = {'platform': platform, 'safe_failure_category': 'NONE'}
    if platform == 'mynavi':
        mynavi_discovery.validate(snapshot)
    if platform in {'type', 'doda'} and not isinstance(snapshot, dict):
        raise ValueError('Invalid snapshot structure')
    if login_url(url, platform) or (allowed(url, platform) and snapshot.get('login')):
        return failure(platform, 'NEEDS_LOGIN')
    if not allowed(url, platform):
        return dict(result, safe_failure_category='DOMAIN_BLOCKED')
    if platform == 'mynavi':
        return mynavi_discovery.evidence(url, snapshot)
    if platform == 'doda':
        validate_findy_snapshot(snapshot)
        for field in ('login', 'busy', 'spa'):
            if field in snapshot and not isinstance(snapshot[field], bool):
                raise ValueError('Invalid doda state structure')
        return doda_evidence(url, snapshot)
    if platform in {'findy', 'type'}:
        validate_findy_snapshot(snapshot)
    result['route_path'] = path_pattern(url, platform=platform)
    links = [link for link in snapshot.get('links', []) if allowed(link, platform)]
    candidates = sorted({path_pattern(link) for link in links
                         if any(s in {'job', 'jobs', 'detail'} for s in urlsplit(link).path.split('/'))})
    keys = {key for link in [url, *links] for key, _ in parse_qsl(urlsplit(link).query)}
    result.update(
        candidate_job_link_patterns=candidates[:30],
        stable_id_candidates=['numeric_path_segment'] if any(':id' in s for s in candidates) else [],
        pagination_mode=('page_parameter_candidate' if keys & {'page', 'p'} else
                         'cursor_parameter_candidate' if keys & {'cursor', 'offset'} else 'unknown'),
        visible_section_headings_or_field_labels=sorted(set(snapshot.get('labels', [])) & LABELS),
        loading_state=snapshot.get('ready') if snapshot.get('ready') in {'loading', 'interactive', 'complete'} else 'unknown',
        busy=snapshot.get('busy') is True,
        spa_marker_present=snapshot.get('spa') is True,
    )
    if platform == 'findy':
        result.update(findy_evidence(url, snapshot, links))
        if exact_detail(url) and 'findy_structure' in snapshot:
            result['findy_structure'] = sanitize_findy(snapshot['findy_structure'])
        candidates = result['job_link_patterns']
        result['candidate_job_link_patterns'] = candidates
        result['stable_id_candidates'] = []
    elif platform == 'type':
        result.update(type_evidence(url, snapshot, links))
        candidates = result['candidate_job_link_patterns']
        result['stable_id_candidates'] = []
    elif platform == 'forkwell':
        result.update(forkwell_evidence(url, snapshot, links))
    elif platform == 'lapras':
        result.update(forkwell_evidence(url, snapshot, links, platform='lapras'))
        candidates = result['job_link_patterns']
        result['candidate_job_link_patterns'] = candidates
        result['stable_id_candidates'] = (
            ['numeric_path_segment'] if '/jobs/:id' in candidates
            or result['stable_id_path_segment'] else [])
        if lapras_numeric_detail(platform, url) and 'lapras_structure' in snapshot:
            result['lapras_structure'] = sanitize(snapshot['lapras_structure'])
    if result['busy'] or result['loading_state'] != 'complete':
        result['safe_failure_category'] = 'LOADING'
    elif (platform == 'findy' and result['page_kind'] == 'other') or (
            not candidates and result.get('page_kind') not in {'detail', 'search-entry'}):
        result['safe_failure_category'] = 'NO_JOB_LINK_EVIDENCE'
    return result


DODA_LIST_PREFIX = '/DodaFront/View/JobSearchList/'
DODA_LIST_PATTERN = DODA_LIST_PREFIX + ':criteria'
DODA_DETAIL_PATTERN = '/DodaFront/View/JobSearchDetail/j_jid__:id/-tab__:tab/'
DODA_PAGE_PATTERN = DODA_LIST_PATTERN + '/-page__:number/'


def doda_detail(url):
    """Internal captures only; identity values never leave discovery."""
    return re.fullmatch(
        r'/DodaFront/View/JobSearchDetail/j_jid__([0-9]+)/-tab__([A-Za-z0-9_-]+)/',
        urlsplit(url).path)


def doda_diagnostic_route(url):
    return (allowed(url, 'doda')
            and urlsplit(url).path.startswith('/DodaFront/View/JobSearchDetail/')
            and not doda_detail(url))


def doda_evidence(url, snapshot):
    """Fixed Stage A route/link evidence, without production Search behavior."""
    detail = doda_detail(url)
    is_list = urlsplit(url).path.startswith(DODA_LIST_PREFIX)
    tab = detail[2] if detail and detail[2] in {'pr', 'jd'} else 'other'
    links = [link for link in snapshot.get('links', []) if allowed(link, 'doda')]
    jobs = {(match[1], match[2]) for link in links if (match := doda_detail(link))}
    canonical = snapshot.get('canonical')
    canonical_detail = doda_detail(canonical) if allowed(canonical, 'doda') else None
    same = bool(detail and canonical_detail and detail[1] == canonical_detail[1])
    targets = [url, *links, *(item['url'] for item in snapshot.get('pagination', [])
                              if allowed(item['url'], 'doda'))]
    page_suffix = any(urlsplit(target).path.startswith(DODA_LIST_PREFIX)
                      and re.search(r'/-page__[0-9]+/$', urlsplit(target).path)
                      for target in targets)
    loading = snapshot.get('ready') if snapshot.get('ready') in {
        'loading', 'interactive', 'complete'} else 'unknown'
    kind = ('detail-preview' if tab == 'pr' else 'detail-jd' if tab == 'jd' else 'other') if detail else 'list' if is_list else 'other'
    category = ('LOADING' if snapshot.get('busy') is True or loading != 'complete'
                else 'NO_JOB_LINK_EVIDENCE' if kind == 'other' else 'NONE')
    # An incomplete or unrecognized page cannot establish a stable identity.
    same = same and category == 'NONE'
    result = {
        'platform': 'doda', 'safe_failure_category': category,
        'route_path': DODA_DETAIL_PATTERN if detail else DODA_LIST_PATTERN if is_list else '/:unrecognized',
        'page_kind': kind, 'detail_tab': tab if detail else None,
        'job_link_count': len(jobs),
        'job_link_patterns': [DODA_DETAIL_PATTERN] if jobs else [],
        'candidate_job_link_patterns': [DODA_DETAIL_PATTERN] if jobs else [],
        'job_link_tabs': sorted({t if t in {'pr', 'jd'} else 'other' for _, t in jobs}),
        'same_jid_pr_jd_present': any((jid, 'jd') in jobs for jid, t in jobs if t == 'pr'),
        'stable_identity_candidate': 'jid' if same else None,
        'canonical_url_pattern': DODA_DETAIL_PATTERN if same else None,
        'page_suffix_present': page_suffix,
        'pagination_candidates': ['page-number'] if page_suffix else [],
        'pagination_link_patterns': [DODA_PAGE_PATTERN] if page_suffix else [],
        'visible_section_headings_or_field_labels': sorted(set(snapshot.get('labels', [])) & LABELS),
        'loading_state': loading, 'busy': snapshot.get('busy') is True,
        'spa_marker_present': snapshot.get('spa') is True,
    }
    if doda_diagnostic_route(url):
        result['doda_detail_structure'] = sanitize_doda(snapshot.get('doda_detail_structure'))
    return result


# Fixed safe enumeration; adding a key does not implement pagination.
TYPE_PAGINATION_KEYS = PAGINATION_KEYS | frozenset({'pageNo', 'pageNum', 'pageNumber'})
TYPE_DETAIL_PATTERN = '/job-:id/:id_detail/'


def type_detail(url):
    return re.fullmatch(r'/job-[0-9]+/[0-9]+_detail/', urlsplit(url).path) is not None


def type_pattern(url):
    if type_detail(url):
        return TYPE_DETAIL_PATTERN
    if re.fullmatch(r'/job-[0-9]+/', urlsplit(url).path):
        return '/job-:id/'
    return path_pattern(url)


def type_evidence(url, snapshot, links):
    """Stage A structural evidence only; never a production identity selector."""
    detail = type_detail(url)
    path = urlsplit(url).path
    job_paths = {urlsplit(link).path for link in links if type_detail(link)}
    structural = [link for link in links if type_detail(link)
                  or urlsplit(link).path.startswith(('/job/', '/jobs/'))
                  or re.fullmatch(r'/job-[0-9]+/', urlsplit(link).path)]
    canonical = snapshot.get('canonical')
    same = (detail and allowed(canonical, 'type') and type_detail(canonical)
            and urlsplit(canonical).path == path)
    pagination = [(item['url'], item['kind']) for item in snapshot.get('pagination', [])
                  if item['kind'] in {'next', 'prev', 'page-number', 'load-more'}
                  and allowed(item['url'], 'type')]
    kinds = {kind for _, kind in pagination}
    if 'load-more' in snapshot.get('controls', []):
        kinds.add('load-more')
    return {
        'route_path': type_pattern(url),
        'page_kind': 'detail' if detail else 'search-entry' if path == '/job/search/'
                     else 'list' if job_paths else 'other',
        'job_link_count': len(job_paths),
        'job_link_patterns': [TYPE_DETAIL_PATTERN] if job_paths else [],
        'candidate_job_link_patterns': sorted({type_pattern(link) for link in structural
                                              if urlsplit(link).path != '/job/search/'}),
        'search_entry_link_patterns': ['/job/search/'] if any(
            urlsplit(link).path == '/job/search/' for link in links) else [],
        'category_id_path_segment': 1 if detail else None,
        'job_id_path_segment': 2 if detail else None,
        'stable_identity_candidate': 'category_plus_job_id' if same else None,
        'canonical_url_pattern': TYPE_DETAIL_PATTERN if same else None,
        'pagination_query_keys': sorted({key for target in [url, *links, *(t for t, _ in pagination)]
                                         for key, _ in parse_qsl(urlsplit(target).query)
                                         if key in TYPE_PAGINATION_KEYS}),
        'pagination_link_patterns': sorted({type_pattern(target) for target, _ in pagination}),
        'pagination_candidates': sorted(kinds),
    }


def findy_detail(url):
    """Exact observed shape; neither the company ID nor opaque key is emitted."""
    return re.fullmatch(r'/companies/[0-9]+/jobs/[A-Za-z0-9_-]+', urlsplit(url).path) is not None


def validate_findy_snapshot(snapshot):
    """Reject malformed structures before producing any discovery evidence."""
    for field in ('links', 'labels', 'controls'):
        values = snapshot.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('Invalid snapshot structure')
    pagination = snapshot.get('pagination', [])
    if not isinstance(pagination, list) or any(
        not isinstance(item, dict) or not isinstance(item.get('url'), str)
        or not isinstance(item.get('kind'), str) for item in pagination
    ):
        raise ValueError('Invalid pagination structure')
    if snapshot.get('canonical') is not None and not isinstance(snapshot['canonical'], str):
        raise ValueError('Invalid canonical structure')


def findy_evidence(url, snapshot, links):
    """Findy discovery only: fixed structural enums, no persisted job identity."""
    detail = findy_detail(url)
    job_links = [link for link in links if findy_detail(link)]
    # Reuse fixed-key evidence without adopting generic route or ID guesses.
    canonical = snapshot.get('canonical')
    same_canonical = (detail and allowed(canonical, 'findy')
                      and urlsplit(canonical).path == urlsplit(url).path)
    pagination_links = [item['url'] for item in snapshot.get('pagination', [])
                        if item['kind'] in {'next', 'prev', 'page-number', 'load-more'}
                        and allowed(item['url'], 'findy')]
    kinds = {item['kind'] for item in snapshot.get('pagination', [])
             if item['kind'] in {'next', 'prev', 'page-number', 'load-more'}
             and allowed(item['url'], 'findy')}
    if 'load-more' in snapshot.get('controls', []):
        kinds.add('load-more')
    return {
        'route_path': '/companies/:id/jobs/:segment' if detail else path_pattern(url, platform='findy'),
        'page_kind': 'detail' if detail else 'list' if urlsplit(url).path == '/recommends' else 'other',
        'job_link_count': len(job_links),
        'job_link_patterns': ['/companies/:id/jobs/:segment'] if job_links else [],
        'company_id_path_segment': 2 if detail or job_links else None,
        'job_key_path_segment': 4 if detail else None,
        'job_key_kind': 'opaque_segment' if detail else None,
        'stable_identity_candidate': 'company_id_plus_job_key' if same_canonical else None,
        'canonical_url_pattern': '/companies/:id/jobs/:segment' if same_canonical else None,
        'pagination_query_keys': sorted({key for target in [url, *links, *pagination_links]
                                         for key, _ in parse_qsl(urlsplit(target).query)
                                         if key in PAGINATION_KEYS}),
        'pagination_link_patterns': sorted({path_pattern(link, platform='findy')
                                            for link in pagination_links}),
        'pagination_candidates': sorted(kinds),
    }


def forkwell_id_segment(url):
    """One-based nonempty segment position, restricted to observed job routes."""
    parts = [part for part in urlsplit(url).path.split('/') if part]
    if (len(parts) in {2, 3} and parts[-2] == 'jobs'
            and re.fullmatch(r'[0-9]+', parts[-1])):
        return len(parts)
    return None


def lapras_job_segment(url):
    """Observed single-segment job route; a slug is not a proven stable ID."""
    parts = [part for part in urlsplit(url).path.split('/') if part]
    if (len(parts) == 2 and parts[0] == 'jobs'
            and parts[1] not in SEGMENTS
            and re.fullmatch(r'[A-Za-z0-9_-]+', parts[1])):
        return 2
    return None


def forkwell_evidence(url, snapshot, links, *, platform='forkwell'):
    """Shared redacted route evidence; never a production adapter selector."""
    if not isinstance(snapshot.get('pagination', []), list):
        raise ValueError('Invalid pagination structure')
    segment = lapras_job_segment if platform == 'lapras' else forkwell_id_segment
    job_links = [link for link in links if segment(link) is not None]
    position = segment(url)
    source_route = platform == 'lapras' and urlsplit(url).path.rstrip('/') == '/jobs/home'
    result = {'page_kind': 'detail' if position else 'list' if source_route or job_links else 'other',
              'job_link_count': len(job_links),
              'job_link_patterns': sorted({path_pattern(link) for link in job_links}),
              'stable_id_path_segment': position,
              'canonical_url_pattern': None,
              'pagination_link_patterns': [], 'pagination_query_keys': [],
              'pagination_candidates': []}
    if platform == 'lapras':
        result['stable_id_path_segment'] = (
            position if position and path_pattern(url) == '/jobs/:id' else None)
    canonical = snapshot.get('canonical')
    if canonical and allowed(canonical, platform):
        # A canonical can support the detail route only if it names the same job.
        if position and urlsplit(canonical).path.rstrip('/') == urlsplit(url).path.rstrip('/'):
            result['canonical_url_pattern'] = path_pattern(canonical)
    pagination = []
    for item in snapshot.get('pagination', []):
        if not isinstance(item, dict):
            continue
        target = item.get('url')
        kind = item.get('kind')
        kinds = {'next', 'prev', 'page-number'}
        if platform == 'lapras':
            kinds.add('load-more')
        if kind in kinds and allowed(target, platform):
            pagination.append((target, kind))
    # Query parameters alone are weak evidence; only fixed keys may leave the probe.
    result['pagination_query_keys'] = sorted({key for target in [url, *links, *(t for t, _ in pagination)]
        for key, _ in parse_qsl(urlsplit(target).query) if key in PAGINATION_KEYS})
    result['pagination_link_patterns'] = sorted({path_pattern(target) for target, _ in pagination})
    result['pagination_candidates'] = sorted({kind for _, kind in pagination})
    if platform == 'lapras':
        controls = snapshot.get('controls', [])
        if not isinstance(controls, list):
            raise ValueError('Invalid controls structure')
        if 'load-more' in controls:
            result['pagination_candidates'] = sorted(set(result['pagination_candidates']) | {'load-more'})
    return result


def collect(browser, platforms):
    """Read existing tabs only; never navigate, click, reload or close user tabs."""
    output = []
    for platform in platforms:
        pages = [p for context in browser.contexts for p in context.pages if allowed(p.url, platform)]
        if not pages:
            output.append({'platform': platform, 'safe_failure_category': 'NO_OPEN_TAB'})
        for page in pages[:10]:
            try:
                before = page.url
                if login_url(before, platform):
                    output.append(failure(platform, 'NEEDS_LOGIN'))
                    continue
                script = (MYNAVI_SNAPSHOT if platform == 'mynavi' else
                          DODA_DETAIL_SNAPSHOT if platform == 'doda' and doda_diagnostic_route(before) else
                          FINDY_DETAIL_SNAPSHOT if platform == 'findy' and exact_detail(before) else
                          LAPRAS_DETAIL_SNAPSHOT if lapras_numeric_detail(platform, before) else TYPE_SNAPSHOT if platform == 'type' else SNAPSHOT)
                snapshot = page.evaluate(script)
                if login_url(page.url, platform) or (allowed(page.url, platform) and snapshot.get('login')):
                    output.append(failure(platform, 'NEEDS_LOGIN'))
                elif page.url != before:
                    output.append({'platform': platform, 'safe_failure_category': 'PAGE_CHANGED'})
                else:
                    output.append(evidence(platform, before, snapshot))
            except Exception:
                output.append(failure(platform, 'NEEDS_LOGIN' if login_url(page.url, platform) else 'READ_FAILED'))
    return output


def local_endpoint(value):
    try:
        p = urlsplit(value)
        if (p.scheme == 'http' and p.hostname in {'127.0.0.1', 'localhost', '::1'}
                and p.port and not p.username and not p.password and p.path in {'', '/'}
                and not p.query and not p.fragment):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('CDP 仅允许本机 HTTP 地址及明确端口。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='手动只读平台调查：读取既有 Chrome 标签页，输出脱敏 JSON；不导航、不登录、0 模型调用。')
    parser.add_argument('--cdp-endpoint', required=True, type=local_endpoint, help='已打开 Chrome 的本机 CDP 地址')
    parser.add_argument('--platform', action='append', choices=tuple(PLATFORMS), help='可重复；默认顺序调查六个平台')
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(args.cdp_endpoint, timeout=10000)
            # Stopping Playwright disconnects; never close the user's browser.
            rows = collect(browser, args.platform or list(PLATFORMS))
    except Exception:
        print(json.dumps({'safe_failure_category': 'CDP_UNAVAILABLE'}))
        return 1
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    for row in platform_summary(rows, args.platform or list(PLATFORMS)):
        # Keep stdout JSON compatible; summaries are fixed, redacted CLI messages.
        print(json.dumps(row, ensure_ascii=False), file=sys.stderr)
    return 0 if all(row['safe_failure_category'] == 'NONE' for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
