from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
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
    tier: str = "All",
) -> HTMLResponse:
    try:
        filters = DashboardFilters(
            q=q, platform=platform, verdict=verdict, provider=provider, days=days, tier=tier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now()
    results = search_evaluations(request.app.state.db_path, filters, now=now)
    summary = summarize_evaluations(request.app.state.db_path, filters, now=now)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cards": evaluation_cards(results), "filters": filters,
            "summary": summary, "scope": dashboard_scope(filters),
            "platform_options": PLATFORMS, "verdict_options": VERDICTS,
            "provider_options": PROVIDERS, "day_options": DAY_RANGES,
            "tier_options": TIER_OPTIONS,
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
def search_pool(request: Request, status: str = "ACTIVE") -> HTMLResponse:
    from scout_agent.search import SearchStore
    from scout_agent.storage.db import Database
    if status not in ('ACTIVE', 'APPLIED', 'EXCLUDED', 'ALL'):
        raise HTTPException(422, "人工状态过滤无效")
    states = {}
    # Do not migrate from a GET route; old databases simply have no search pool.
    if not request.app.state.db_path.exists():
        results = []
    else:
        with Database(request.app.state.db_path, read_only=True) as db:
            exists = db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_jobs'").fetchone()
            store = SearchStore(db, migrate=False)
            results = store.current_results() if exists else []
            states = store.user_states()
    from .viewmodels import search_cards
    from .search_runs import KEYWORDS, LIMITS
    cards = search_cards(results, states)
    counts = {key: sum(card['user_status'] == key for card in cards)
              for key in ('ACTIVE', 'APPLIED', 'EXCLUDED')}
    counts['ALL'] = len(cards)
    return templates.TemplateResponse(request=request, name="search.html", context={
        "counts": counts, "selected_status": status,
        "cards": [card for card in cards if status == 'ALL' or card['user_status'] == status], "sources": KEYWORDS, "limits": LIMITS,
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
