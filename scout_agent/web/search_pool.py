"""Read-only Search pool presentation; no migrations or classifier calls."""
from urllib.parse import urlencode

from scout_agent.search import SearchStore
from scout_agent.storage.db import Database

STATUSES = ('ACTIVE', 'APPLIED', 'EXCLUDED', 'ALL')
VERDICTS = ('RECOMMENDED', 'TARGET', 'POSSIBLE', 'DROP', 'ALL')
PLATFORMS = ('ALL', 'green', 'forkwell', 'lapras', 'findy', 'type', 'doda', 'mynavi')
PAGE_SIZE = 20


def pool_url(status, verdict, platform, page=1):
    return '/search?' + urlencode(dict(status=status, verdict=verdict, platform=platform, page=page))


def query_pool(path, status, verdict, platform, page):
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
        return ((s == 'ALL' or states.get(job.job_id, 'ACTIVE') == s)
                and (v == 'ALL' or (result.verdict in ('TARGET', 'POSSIBLE') if v == 'RECOMMENDED' else result.verdict == v))
                and (p == 'ALL' or (job.platform or 'green') == p))

    counts = {
        'status': {s: sum(matches(item, s=s) for item in results) for s in STATUSES},
        'verdict': {v: sum(matches(item, v=v) for item in results) for v in VERDICTS},
        'platform': {p: sum(matches(item, p=p) for item in results) for p in PLATFORMS},
    }
    rows = [item for item in results if matches(item)]
    # Stable sorts: job ID breaks ties, NULL timestamps follow known timestamps.
    rows.sort(key=lambda item: item[0].job_id)
    rows.sort(key=lambda item: seen.get(item[0].job_id) or '', reverse=True)
    rows.sort(key=lambda item: {'TARGET': 0, 'POSSIBLE': 1, 'DROP': 2}[item[1].verdict])
    total = len(rows)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    current = min(page, pages)
    return dict(results=rows[(current - 1) * PAGE_SIZE:current * PAGE_SIZE], states=states,
                facets=counts, total=total, page=current, pages=pages)


def source_label(platform, source):
    if platform == 'green' and source in ('AWS', 'SRE', 'DevOps', 'Terraform', 'Kubernetes'):
        return source + ' 相关职位'
    if platform in ('green', 'doda', 'mynavi') and source == 'インフラエンジニア':
        return source + '职种'
    if platform == 'type':
        return source + '（职种入口）'
    return {('forkwell', '求人一覧'): '全部求人', ('lapras', '求人検索'): '求人搜索首页',
            ('findy', 'おすすめ求人'): '推荐求人'}.get((platform, source), source)
