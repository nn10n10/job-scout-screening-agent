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
