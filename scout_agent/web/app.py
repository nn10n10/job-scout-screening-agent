from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import router


WEB_HOST = "127.0.0.1"
WEB_PORT = 8765


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI(
        title="Job Scout WebUI",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.db_path = db_path
    app.include_router(router)
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    return app


def run_web(db_path: Path) -> None:
    """Bind only to loopback; never expose this unauthenticated UI on a network."""
    uvicorn.run(create_app(db_path), host=WEB_HOST, port=WEB_PORT, access_log=False)
