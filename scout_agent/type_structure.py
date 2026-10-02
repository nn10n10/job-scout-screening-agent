"""Content-free Type source diagnostics, independent of production parsing."""
import json

from scout_agent.platform_discovery import LABELS

COUNTS = ('visible_anchor_count_capped', 'exact_detail_link_count',
          'job_category_link_count', 'job_opaque_link_count',
          'search_entry_link_count', 'same_origin_anchor_count')
ROUTES = ('job-category', 'job-search', 'other')

SOURCE_STRUCTURE = r"""() => {
 const labels = LABELS_JSON;
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const kind = path => /^\/job-[0-9]+\/$/.test(path) ? 'job-category' :
   path === '/job/search/' ? 'job-search' : /^\/job-[0-9]+\/[0-9]+_detail\/$/.test(path) ? 'detail' : 'other';
 const parse = href => { try { return new URL(href, location.href); } catch { return null; } };
 const same = u => u && u.origin === 'https://type.jp';
 const anchors = Array.from(document.querySelectorAll('a[href]')).filter(visible);
 const urls = anchors.map(e => parse(e.getAttribute('href'))).filter(same);
 const exact = u => !u.search && !u.hash;
 const canonical = document.querySelector('link[rel~="canonical"]');
 const canonicalURL = canonical ? parse(canonical.getAttribute('href')) : null;
 const route = location.origin === 'https://type.jp' ? kind(location.pathname) : 'other';
 return {
   route_kind: ['job-category','job-search'].includes(route) ? route : 'other',
   ready: document.readyState,
   busy: !!Array.from(document.querySelectorAll('[aria-busy="true"]')).find(visible),
   visible_anchor_count_capped: Math.min(2000, anchors.length),
   exact_detail_link_count: urls.filter(u => exact(u) && kind(u.pathname) === 'detail').length,
   job_category_link_count: urls.filter(u => exact(u) && kind(u.pathname) === 'job-category').length,
   job_opaque_link_count: urls.filter(u => exact(u) && /^\/job\/[A-Za-z0-9_-]+\/$/.test(u.pathname) && u.pathname !== '/job/search/').length,
   search_entry_link_count: urls.filter(u => exact(u) && kind(u.pathname) === 'job-search').length,
   same_origin_anchor_count: urls.length,
   has_canonical: !!canonical,
   canonical_kind: !canonical ? 'none' : same(canonicalURL) && exact(canonicalURL) ? kind(canonicalURL.pathname) : 'other',
   visible_fixed_labels: [...new Set(Array.from(document.querySelectorAll('h1,h2,h3,h4,dt,label,th')).filter(visible)
     .map(e => (e.textContent || '').trim()).filter(t => labels.includes(t)))]
 };
}""".replace('LABELS_JSON', json.dumps(sorted(LABELS), ensure_ascii=False))


def sanitize(raw):
    """Rebuild the schema, never forward arbitrary fields or string values."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid Type structure')

    def enum(key, values, fallback):
        value = raw.get(key)
        return value if isinstance(value, str) and value in values else fallback

    result = {
        'route_kind': enum('route_kind', ROUTES, 'other'),
        'ready': enum('ready', ('loading', 'interactive', 'complete'), 'loading'),
        'busy': raw.get('busy') is True,
    }
    for key in COUNTS:
        value = raw.get(key)
        result[key] = min(value, 2000) if type(value) is int and value >= 0 else 0
    result.update(has_canonical=raw.get('has_canonical') is True,
                  canonical_kind=enum('canonical_kind', (*ROUTES, 'detail', 'none'), 'other'))
    labels = raw.get('visible_fixed_labels')
    result['visible_fixed_labels'] = sorted({s for s in labels if isinstance(s, str) and s in LABELS}) if isinstance(labels, list) else []
    return result
