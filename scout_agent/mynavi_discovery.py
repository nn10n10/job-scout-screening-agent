"""Fixed structural Mynavi evidence only; no production Search behavior."""
import re
from urllib.parse import urlsplit, parse_qsl

PATTERN = '/jobinfo-:id4/'
SELECTORS = ('h1 .occName', 'h1 .companyName', '.jobPointArea__wrap-jobDescription', 'table.jobOfferTable')
QUERY_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'ty')


def jobinfo(url):
    return re.fullmatch(r'/jobinfo-([0-9]+)-([0-9]+)-([0-9]+)-([0-9]+)/', urlsplit(url).path)


def pg(path):
    return bool(re.search(r'/pg[0-9]+/$', path))


def shape(path):
    base = re.sub(r'/pg[0-9]+/$', '/', path)
    if re.fullmatch(r'/engineer/(list|ft)/[^/]+/', base):
        return '/engineer/' + base.split('/')[2] + '/:criteria/'
    if re.fullmatch(r'/[^/]+/[^/]+/', base):
        return '/:segment/:segment/'
    return 'other'


def validate(snapshot):
    from scout_agent.platform_discovery import validate_findy_snapshot
    if not isinstance(snapshot, dict):
        raise ValueError('Invalid snapshot')
    validate_findy_snapshot(snapshot)
    for field in ('login', 'busy', 'spa'):
        if field in snapshot and not isinstance(snapshot[field], bool):
            raise ValueError('Invalid state')
    if 'ready' in snapshot and (not isinstance(snapshot['ready'], str) or snapshot['ready'] not in {'loading', 'interactive', 'complete'}):
        raise ValueError('Invalid ready state')
    if 'mynavi_detail_structure' in snapshot:
        structure = snapshot['mynavi_detail_structure']
        if (not isinstance(structure, dict) or set(structure) != set(SELECTORS)
                or any(not isinstance(v, bool) for v in structure.values())):
            raise ValueError('Invalid detail structure')


def evidence(url, snapshot):
    from scout_agent.platform_discovery import allowed, failure, LABELS
    if snapshot.get('ready') != 'complete' or snapshot.get('busy') is True:
        return failure('mynavi', 'LOADING')
    path = urlsplit(url).path
    detail = jobinfo(url)
    route_shape = shape(path)
    kind = 'jobinfo-detail' if detail else 'source-list-candidate' if route_shape != 'other' else 'other'
    links = [link for link in snapshot.get('links', [])[:2000] if allowed(link, 'mynavi')]
    jobs = [link for link in links if jobinfo(link)]
    candidates = [link for link in links if re.fullmatch(r'/job/[^/]+/', urlsplit(link).path)]
    patterns = ([PATTERN] if jobs else []) + (['/job/:segment/'] if candidates else [])
    canonical = snapshot.get('canonical')
    same = bool(detail and allowed(canonical, 'mynavi') and jobinfo(canonical)
                and jobinfo(canonical).groups() == detail.groups())
    keys = {key for target in [*jobs, *([url] if detail else [])]
            for key, _ in parse_qsl(urlsplit(target).query, keep_blank_values=True)}
    pagination = [item['url'] for item in snapshot.get('pagination', [])[:2000]
                  if item['kind'] in {'next', 'prev', 'page-number'} and allowed(item['url'], 'mynavi')]
    suffix = any(pg(urlsplit(target).path) for target in [url, *pagination])
    result = {
        'platform': 'mynavi', 'safe_failure_category': 'NONE' if detail or patterns else 'NO_JOB_LINK_EVIDENCE',
        'page_kind': kind, 'route_path': PATTERN if detail else route_shape,
        'jobinfo_pattern': PATTERN if detail or jobs else None,
        'jobinfo_link_count': len(jobs), 'job_link_count': len(candidates),
        'jobinfo_query_present_count': sum(bool(urlsplit(link).query) for link in jobs),
        'jobinfo_fragment_present_count': sum(bool(urlsplit(link).fragment) for link in jobs),
        'candidate_job_link_patterns': patterns, 'fixed_anchor_patterns': patterns,
        'jobinfo_query_keys_present': {key: key in keys for key in QUERY_KEYS},
        'other_query_key_present': bool(keys - set(QUERY_KEYS)),
        'stable_identity_candidate': 'jobinfo-id4' if same else None,
        'canonical_url_pattern': PATTERN if same else None,
        'pagination_mode': 'path-pg-candidate' if suffix else 'unknown',
        'page_suffix_present': suffix, 'pagination_link_patterns': ['/:source/pg:digits/'] if suffix else [],
        'visible_section_headings_or_field_labels': sorted(set(snapshot.get('labels', [])) & LABELS),
        'loading_state': 'complete', 'busy': False, 'spa_marker_present': snapshot.get('spa') is True,
    }
    if not detail:
        parts = [part for part in path.split('/') if part]
        result.update(nonempty_segment_count_capped=min(len(parts), 10),
                      fixed_segments_present=sorted(set(parts) & {'engineer', 'list', 'ft', 'search'}),
                      has_pg_suffix=pg(path), source_route_shape=route_shape)
    if detail and 'mynavi_detail_structure' in snapshot:
        result['mynavi_detail_structure'] = snapshot['mynavi_detail_structure'].copy()
    return result
