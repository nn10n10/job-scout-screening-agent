"""Manual, model-free discovery of already open authenticated tabs."""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlsplit, parse_qsl

PLATFORMS = {
    'forkwell': frozenset({'jobs.forkwell.com', 'forkwell.com'}),
    'findy': frozenset({'findy-code.io'}),
    'lapras': frozenset({'lapras.com'}),
    'type': frozenset({'type.jp'}),
    'doda': frozenset({'doda.jp'}),
    'mynavi': frozenset({'tenshoku.mynavi.jp'}),
}
SEGMENTS = frozenset({'jobs', 'job', 'search', '求人', 'companies', 'company',
                      'career', 'careers', 'list', 'detail', 'index', 'view'})
LABELS = frozenset({'仕事内容', '応募資格', '必須要件', '歓迎要件', '給与', '勤務地',
                    '勤務時間', '雇用形態', '福利厚生', '開発環境', '技術', '検索結果',
                    '求人検索', '募集要項', 'リモート', '年収'})
# No body text, anchor text, profile data, cookies or storage are read.
SNAPSHOT = """() => {
 const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
 const login = Array.from(document.querySelectorAll('input[type="password"],form[action],h1,h2,button,[role="button"]'))
   .filter(visible).some(e => e.matches('input[type="password"]') ||
     /(?:^|\\/)(?:login|signin|sign-in|sign_in)(?:\\/|$|\\?)/i.test(e.getAttribute('action') || '') ||
     /^(?:ログイン|サインイン|登录|Sign in|Log in|Googleでログイン|Googleでサインイン|Sign in with Google|Session expired|セッションの有効期限が切れました|登录已失效)$/i.test((e.textContent || '').trim()));
 if (login) return {login: true};
 const labels = %s;
 return {
   links: Array.from(document.querySelectorAll('a[href]')).filter(visible).slice(0, 2000).map(e => e.href),
   labels: Array.from(document.querySelectorAll('h1,h2,h3,h4,dt,label,th')).filter(visible)
     .map(e => (e.textContent || '').trim()).filter(t => labels.includes(t)),
   ready: document.readyState,
   busy: !!document.querySelector('[aria-busy="true"]'),
   spa: !!document.querySelector('#__next,#__nuxt,[data-reactroot]'),
   login: false
 };
}""" % json.dumps(sorted(LABELS), ensure_ascii=False)


def allowed(url, platform):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.netloc in PLATFORMS[platform]
                and not re.search(r'[\s\x00-\x1f\x7f]', url))
    except (ValueError, TypeError):
        return False


def path_pattern(url):
    """Never retain arbitrary literal path segments, query values or fragments."""
    p = urlsplit(url)
    parts = p.path.split('/')
    return '/'.join(s if s in SEGMENTS else ':id' if re.fullmatch(r'\d+', s)
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
    if login_url(url, platform) or (allowed(url, platform) and snapshot.get('login')):
        return failure(platform, 'NEEDS_LOGIN')
    if not allowed(url, platform):
        return dict(result, safe_failure_category='DOMAIN_BLOCKED')
    result['route_path'] = path_pattern(url)
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
    if result['busy'] or result['loading_state'] != 'complete':
        result['safe_failure_category'] = 'LOADING'
    elif not candidates:
        result['safe_failure_category'] = 'NO_JOB_LINK_EVIDENCE'
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
                snapshot = page.evaluate(SNAPSHOT)
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
