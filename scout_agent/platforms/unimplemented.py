from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from .base import PlatformAdapter


class UnimplementedAdapter(PlatformAdapter):
    """READ-ONLY AUTOMATION: placeholder with no guessed URLs or selectors."""

    platform_name = ""
    scout_list_url = None

    def _unimplemented(self) -> None:
        raise NotImplementedError("Adapter not implemented yet.")

    def is_logged_in(self, page: Page | None) -> bool:
        self._unimplemented()

    def get_scout_list(self, page: Page | None) -> list[Any]:
        self._unimplemented()

    def get_scout_detail(self, page: Page | None, scout: Any) -> Any:
        self._unimplemented()

    def normalize_scout(self, raw: Any):
        self._unimplemented()
