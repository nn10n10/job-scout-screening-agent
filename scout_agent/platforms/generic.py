from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from scout_agent.config import PROJECT_ROOT
from scout_agent.models.scout import Scout
from .base import PlatformAdapter


class GenericAdapter(PlatformAdapter):
    """Offline fixture adapter; no browser or account access."""

    platform_name = "generic"

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path or PROJECT_ROOT / "tests" / "fixtures" / "example_scout.json"

    def is_logged_in(self, page: Page | None) -> bool:
        return True

    def get_scout_list(self, page: Page | None) -> list[dict[str, Any]]:
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else [data]

    def get_scout_detail(self, page: Page | None, scout: dict[str, Any]) -> dict[str, Any]:
        return scout

    def normalize_scout(self, raw: dict[str, Any]) -> Scout:
        return Scout.model_validate({**raw, "platform": self.platform_name})
