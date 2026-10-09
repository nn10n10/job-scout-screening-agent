"""Read-only Search pool presentation; no migrations or classifier calls."""
from urllib.parse import urlencode

from scout_agent.search import SearchStore
from scout_agent.storage.db import Database

STATUSES = ('ACTIVE', 'APPLIED', 'EXCLUDED')
VERDICTS = ('TARGET', 'POSSIBLE', 'DROP')
PLATFORMS = ('green', 'forkwell', 'lapras', 'findy', 'type', 'doda', 'mynavi', 'indeed')
PAGE_SIZES = (10, 20, 50)


def pool_url(status, verdict, platform, page=1, page_size=10):
    return '/search?' + urlencode(dict(status=status, verdict=verdict, platform=platform, page=page, page_size=page_size), doseq=True)


def query_pool(path, status, verdict, platform, page, page_size=10):
    if page_size not in PAGE_SIZES:
        raise ValueError("Invalid pool page size")
    results, states, seen = [], {}, {}
    if path.is_file():
        with Database(path, read_only=True) as db:
            tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'search_jobs', 'search_evaluations'} <= tables:
                store = SearchStore(db, migrate=False)
                results = store.current_results()
                states = store.user_states()
                columns = {row[1] for row in db.conn.execute('PRAGMA table_info(search_jobs)')}
                if 'last_seen_at' in columns:
                    seen = {row[0]: row[1] for row in db.conn.execute('SELECT job_id,last_seen_at FROM search_jobs')}

    def matches(item, s=status, v=verdict, p=platform):
        job, result = item
        return (states.get(job.job_id, 'ACTIVE') in s
                and result.verdict in v and (job.platform or 'green') in p)

    counts = {
        'status': {s: sum(matches(item, s=(s,)) for item in results) for s in STATUSES},
        'verdict': {v: sum(matches(item, v=(v,)) for item in results) for v in VERDICTS},
        'platform': {p: sum(matches(item, p=(p,)) for item in results) for p in PLATFORMS},
    }

    rows = [item for item in results if matches(item)]
    # Stable sorts: job ID breaks ties, NULL timestamps follow known timestamps.
    rows.sort(key=lambda item: item[0].job_id)
    rows.sort(key=lambda item: seen.get(item[0].job_id) or '', reverse=True)
    rows.sort(key=lambda item: {'TARGET': 0, 'POSSIBLE': 1, 'DROP': 2}[item[1].verdict])
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    current = min(page, pages)
    return dict(results=rows[(current - 1) * page_size:current * page_size], states=states,
                facets=counts, total=total, page=current, pages=pages, page_size=page_size)


def source_label(platform, source):
    if platform == 'green' and source in ('AWS', 'SRE', 'DevOps', 'Terraform', 'Kubernetes'):
        return source + ' 相关职位'
    if platform in ('green', 'doda', 'mynavi') and source == 'インフラエンジニア':
        return source + '职种'
    if platform == 'indeed':
        return {'インフラエンジニア': '基础设施工程师', 'クラウドエンジニア': '云工程师', 'SRE': 'SRE', 'DevOps': 'DevOps'}.get(source, source)
    if platform == 'type':
        return source + '（职种入口）'
    return {('forkwell', '求人一覧'): '全部求人', ('lapras', '求人検索'): '求人搜索首页',
            ('findy', 'おすすめ求人'): '推荐求人'}.get((platform, source), source)
