"""Content-free Stage A diagnostics; no production route or identity inference."""
import json

RECRUIT_KEYS = (
    'jobContentOutline', 'jobContentDetail', 'targetMemberOutline', 'targetMemberDetail',
    'projectCase', 'developmentEnvironment', 'workTime', 'holiday', 'salary', 'jobState', 'access',
)
SHAPES = frozenset({'hyphen-key-doubleunderscore-value', 'numeric', 'plain', 'other'})
BOOLS = (
    'has_job_search_detail_prefix', 'jid_segment_present', 'canonical_same_jid_as_route_prefix',
    'has_next_data', 'next_data_jid_present', 'next_data_jid_matches_route_prefix', 'has_recruit_object',
)

STRUCTURE = r"""() => {
 const prefix = '/DodaFront/View/JobSearchDetail/';
 const exact = /^\/DodaFront\/View\/JobSearchDetail\/j_jid__([0-9]+)\/-tab__([A-Za-z0-9_-]+)\/$/;
 if (location.origin !== 'https://doda.jp' || !location.pathname.startsWith(prefix) || exact.test(location.pathname)) return {};
 const parts = location.pathname.split('/').filter(Boolean);
 const jid = /^j_jid__([0-9]+)$/.exec(parts[3] || '')?.[1] || null;
 const tab = /^-tab__(.+)$/.exec(parts[4] || '')?.[1] || null;
 const trailing = parts.slice(5);
 const result = {
   nonempty_segment_count_capped: Math.min(parts.length, 20),
   has_job_search_detail_prefix: true, jid_segment_present: jid !== null,
   tab_segment_kind: tab === null ? 'none' : ['pr','jd'].includes(tab) ? tab : 'other',
   trailing_segment_count_capped: Math.min(trailing.length, 10),
   trailing_segment_shapes: [...new Set(trailing.slice(0, 10).map(s =>
     /^-[A-Za-z0-9_-]+__.+$/.test(s) ? 'hyphen-key-doubleunderscore-value' :
     /^[0-9]+$/.test(s) ? 'numeric' : /^[A-Za-z0-9_-]+$/.test(s) ? 'plain' : 'other'))],
   canonical_kind: 'none', canonical_same_jid_as_route_prefix: false,
   has_next_data: false, next_data_jid_present: false,
   next_data_jid_matches_route_prefix: false, has_recruit_object: false,
   recruit_key_presence: {}
 };
 const keys = RECRUIT_KEYS_JSON;
 keys.forEach(k => result.recruit_key_presence[k] = false);
 try {
   const href = document.querySelector('link[rel="canonical"]')?.href;
   if (href) {
     result.canonical_kind = 'other';
     const c = new URL(href);
     if (c.origin === location.origin) {
       const match = exact.exec(c.pathname);
       const extended = /^\/DodaFront\/View\/JobSearchDetail\/j_jid__([0-9]+)\/-tab__[A-Za-z0-9_-]+\/.+$/.exec(c.pathname);
       result.canonical_kind = match ? 'exact-detail' : extended ? 'extended-detail' :
         c.pathname.startsWith('/DodaFront/View/JobSearchList/') ? 'list' : 'other';
       result.canonical_same_jid_as_route_prefix = !!jid && (match || extended)?.[1] === jid;
     }
   }
 } catch (_) { result.canonical_kind = 'other'; }
 try {
   const node = document.querySelector('#__NEXT_DATA__');
   result.has_next_data = !!node;
   if (node) {
     const payload = JSON.parse(node.textContent || '');
     const job = payload?.props?.pageProps?.job?.job;
     const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
     const id = object(job) && (typeof job.jid === 'string' ||
       (Number.isSafeInteger(job.jid) && job.jid >= 0)) ? String(job.jid) : '';
     result.next_data_jid_present = /^[0-9]+$/.test(id);
     result.next_data_jid_matches_route_prefix = result.next_data_jid_present && !!jid && id === jid;
     const recruit = object(job) ? job.recruit : null;
     result.has_recruit_object = object(recruit);
     keys.forEach(k => result.recruit_key_presence[k] = object(recruit) && Object.hasOwn(recruit, k));
   }
 } catch (_) { /* Keep default booleans; never return JSON text or errors. */ }
 return result;
}"""

STRUCTURE = STRUCTURE.replace('RECRUIT_KEYS_JSON', json.dumps(RECRUIT_KEYS))


def sanitize(value):
    """Rebuild every field; never trust extra keys, strings or truthy values."""
    value = value if isinstance(value, dict) else {}
    result = {key: value.get(key) is True for key in BOOLS}
    for key, cap in [('nonempty_segment_count_capped', 20), ('trailing_segment_count_capped', 10)]:
        count = value.get(key)
        result[key] = min(max(count, 0), cap) if type(count) is int else 0
    for key, choices, default in [
        ('tab_segment_kind', {'pr', 'jd', 'other', 'none'}, 'none'),
        ('canonical_kind', {'exact-detail', 'extended-detail', 'list', 'other', 'none'}, 'none'),
    ]:
        candidate = value.get(key)
        result[key] = candidate if isinstance(candidate, str) and candidate in choices else default
    shapes = value.get('trailing_segment_shapes')
    result['trailing_segment_shapes'] = sorted({s for s in shapes if isinstance(s, str) and s in SHAPES}) if isinstance(shapes, list) else []
    presence = value.get('recruit_key_presence')
    presence = presence if isinstance(presence, dict) else {}
    result['recruit_key_presence'] = {key: presence.get(key) is True for key in RECRUIT_KEYS}
    return result
