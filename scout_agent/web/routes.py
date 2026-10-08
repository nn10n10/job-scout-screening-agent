from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .services import (
    DAY_RANGES, PLATFORMS, PROVIDERS, TIER_OPTIONS, VERDICTS, DashboardFilters,
    get_evaluation, search_evaluations, summarize_evaluations,
)
from .viewmodels import dashboard_scope, evaluation_cards, evaluation_detail


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request, q: str = "", platform: str = "All",
    verdict: str = "KEEP,MAYBE", provider: str = "Final", days: str = "7",
    tier: str = "All", page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10), status: list[str] | None = Query(default=None),
) -> HTMLResponse:
    from .services import STATUSES, STATUS_LABELS
    if status is None and 'status_present' in request.query_params:
        raise HTTPException(422, '请至少选择一个人工状态')
    try:
        filters = DashboardFilters(
            q=q, platform=platform, verdict=verdict, provider=provider, days=days, tier=tier,
            status=tuple(status) if status is not None else ("ACTIVE",),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now()
    from dataclasses import asdict
    from urllib.parse import urlencode
    from .services import PAGE_SIZES
    if (not request.query_params.get('page', '1').isascii()
            or not request.query_params.get('page', '1').isdecimal()
            or request.query_params.get('page_size', '10') not in {str(n) for n in PAGE_SIZES}):
        raise HTTPException(422, '分页参数无效')
    summary = summarize_evaluations(request.app.state.db_path, filters, now=now)
    pages = max(1, (summary.total + page_size - 1) // page_size)
    def page_url(number):
        return '/?' + urlencode({**asdict(filters), 'page': number, 'page_size': page_size}, doseq=True)
    if page > pages:
        return RedirectResponse(page_url(pages), status_code=303, headers=PRIVATE_HEADERS)
    results = search_evaluations(request.app.state.db_path, filters, now=now,
                                 page=page, page_size=page_size)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cards": evaluation_cards(results), "filters": filters,
            "details": {r.scout_id: evaluation_detail(r) for r in results},
            "states": {r.scout_id: r.user_status for r in results},
            "status_options": STATUSES, "status_labels": STATUS_LABELS,
            "summary": summary, "scope": dashboard_scope(filters),
            "platform_options": PLATFORMS, "verdict_options": VERDICTS,
            "provider_options": PROVIDERS, "day_options": DAY_RANGES,
            "tier_options": TIER_OPTIONS, "page": page, "pages": pages,
            "page_size": page_size, "page_sizes": PAGE_SIZES, "page_url": page_url,
            "daily_csrf_token": request.app.state.daily_runs.csrf_token,
        },
        headers=PRIVATE_HEADERS,
    )


@router.get("/jobs/{scout_id}", response_class=HTMLResponse)
def job_detail(request: Request, scout_id: int) -> HTMLResponse:
    result = get_evaluation(request.app.state.db_path, scout_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"job": evaluation_detail(result)},
        headers=PRIVATE_HEADERS,
    )


@router.get("/search", response_class=HTMLResponse)
def search_pool(
    request: Request, status: list[str] | None = Query(default=None),
    verdict: list[str] | None = Query(default=None),
    platform: list[str] | None = Query(default=None), page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10),
):
    from .search_pool import PAGE_SIZES, STATUSES, VERDICTS, PLATFORMS, pool_url, query_pool, source_label
    from .viewmodels import search_cards
    from .search_runs import KEYWORDS, LIMITS
    def selection(name, values, allowed, default):
        if values is None:
            if name + '_present' in request.query_params:
                raise HTTPException(422, "请至少选择一个过滤值")
            return default
        if any(value not in allowed for value in values):
            raise HTTPException(422, "候选池过滤参数无效")
        return tuple(value for value in allowed if value in values)

    status = selection('status', status, STATUSES, ('ACTIVE',))
    verdict = selection('verdict', verdict, VERDICTS, ('TARGET', 'POSSIBLE'))
    platform = selection('platform', platform, PLATFORMS, PLATFORMS)
    raw_page = request.query_params.get('page', '1')
    if not raw_page.isascii() or not raw_page.isdecimal():
        raise HTTPException(422, '页码必须为正整数')
    if request.query_params.get("page_size", "10") not in {str(size) for size in PAGE_SIZES}:
        raise HTTPException(422, "每页条数必须为 10、20 或 50")
    pool = query_pool(request.app.state.db_path, status, verdict, platform, page, page_size)
    if page != pool['page']:
        return RedirectResponse(pool_url(status, verdict, platform, pool['page'], page_size), status_code=303,
                                headers=PRIVATE_HEADERS)
    return templates.TemplateResponse(request=request, name="search.html", context={
        **pool, "counts": pool['facets']['status'], "selected_status": status,
        "selected_verdict": verdict, "selected_platform": platform,
        "pool_url": pool_url, "all_statuses": STATUSES, "all_verdicts": VERDICTS,
        "all_platforms": PLATFORMS, "source_label": source_label,
        "source_labels": {p: {s: source_label(p, s) for s in values} for p, values in {
            'green': KEYWORDS, 'forkwell': ['求人一覧'], 'lapras': ['求人検索'],
            'findy': ['おすすめ求人'], 'type': ['サーバ・クラウド（設計・構築）', 'DevOps・SRE'],
            'doda': ['インフラエンジニア'], 'mynavi': ['インフラエンジニア'],
        }.items()},
        "cards": search_cards(pool['results'], pool['states']), "sources": KEYWORDS, "limits": LIMITS,
        "csrf_token": request.app.state.search_runs.csrf_token,
    }, headers=PRIVATE_HEADERS)


@router.post("/search/run")
async def start_search(request: Request):
    import secrets
    manager = request.app.state.search_runs
    token = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(token.encode(), manager.csrf_token.encode()):
        raise HTTPException(403, "CSRF 校验失败")
    try:
        data = await request.json()
        started = manager.start(data)
    except ValueError as exc:
        raise HTTPException(422, "搜索参数无效，请检查 source 和整数范围") from exc
    if not started:
        raise HTTPException(409, "正在搜索，请等待本轮完成")
    from fastapi.responses import JSONResponse
    return JSONResponse(manager.snapshot(), status_code=202, headers=PRIVATE_HEADERS)


@router.get("/search/run/status")
def search_status(request: Request):
    from fastapi.responses import JSONResponse
    return JSONResponse(request.app.state.search_runs.snapshot(), headers=PRIVATE_HEADERS)


@router.post("/search/jobs/{job_id}/state")
async def set_search_job_state(request: Request, job_id: str):
    import secrets
    import sqlite3
    from types import SimpleNamespace
    from fastapi.responses import JSONResponse
    from scout_agent.search import SearchStore
    manager = request.app.state.search_runs
    if not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(),
                                  manager.csrf_token.encode()):
        raise HTTPException(403, "CSRF 校验失败")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(422, "人工状态无效") from None
    if (not isinstance(data, dict) or set(data) != {'status'}
            or data['status'] not in ('ACTIVE', 'APPLIED', 'EXCLUDED')):
        raise HTTPException(422, "人工状态无效")
    # Serialize with start(): no scan can begin between the busy check and commit.
    with manager.lock:
        if manager.state['status'] == 'running':
            raise HTTPException(409, "正在搜索，请等待本轮完成")
        path = request.app.state.db_path
        if not path.exists():
            raise HTTPException(404, "职位不存在")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            SearchStore(SimpleNamespace(conn=conn), migrate=False).set_user_state(job_id, data['status'])
        except KeyError:
            raise HTTPException(404, "职位不存在") from None
        except ValueError:
            raise HTTPException(422, "请先恢复到待处理") from None
        finally:
            conn.close()
    return JSONResponse({'status': data['status']}, headers=PRIVATE_HEADERS)


@router.post("/api/daily/run")
async def start_daily(request: Request):
    import secrets
    from fastapi.responses import JSONResponse
    manager = request.app.state.daily_runs
    if not secrets.compare_digest(request.headers.get('x-csrf-token', '').encode(), manager.csrf_token.encode()):
        raise HTTPException(403, 'CSRF 校验失败')
    from scout_agent.daily import normalize_platforms
    try:
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'platforms'} or not isinstance(data['platforms'], list):
            raise ValueError()
        platforms = normalize_platforms(data['platforms'])
    except (ValueError, TypeError):
        raise HTTPException(422, '请求必须仅包含非空 platforms 数组，且平台必须有效') from None
    if not manager.start(platforms):
        raise HTTPException(409, '正在筛选，请等待本轮完成')
    return JSONResponse(manager.snapshot(), status_code=202, headers=PRIVATE_HEADERS)


@router.get("/api/daily/status")
def daily_status(request: Request):
    return request.app.state.daily_runs.snapshot()


@router.post("/jobs/{scout_id}/state")
async def set_scout_state(request: Request, scout_id: int):
    from contextlib import closing
    import secrets
    import sqlite3
    from fastapi.responses import JSONResponse
    from .services import STATUSES
    manager = request.app.state.daily_runs
    if not secrets.compare_digest(request.headers.get('x-csrf-token', '').encode(), manager.csrf_token.encode()):
        raise HTTPException(403, 'CSRF 校验失败')
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(422, '人工状态无效') from None
    if not isinstance(data, dict) or set(data) != {'status'} or data['status'] not in STATUSES:
        raise HTTPException(422, '人工状态无效')
    with manager.lock:
        if manager.state['state'] == 'running':
            raise HTTPException(409, '正在筛选，请等待本轮完成')
        path = request.app.state.db_path
        if not path.is_file():
            raise HTTPException(404, '职位不存在')
        try:
            with closing(sqlite3.connect(path)) as conn, conn:
                if not conn.execute('SELECT 1 FROM evaluations WHERE scout_id=?', (scout_id,)).fetchone():
                    raise HTTPException(404, '职位不存在')
                conn.execute("""CREATE TABLE IF NOT EXISTS scout_user_states (
                    scout_id INTEGER PRIMARY KEY REFERENCES scouts(id),
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE','APPLIED','EXCLUDED')),
                    updated_at TEXT NOT NULL)""")
                conn.execute("""INSERT INTO scout_user_states VALUES (?,?,?)
                    ON CONFLICT(scout_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at""",
                    (scout_id, data['status'], datetime.now().isoformat()))
        except sqlite3.Error:
            raise HTTPException(409, '本地状态暂时无法保存，请稍后重试') from None
    return JSONResponse({'status': data['status']}, headers=PRIVATE_HEADERS)
