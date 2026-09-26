from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from playwright.sync_api import Page

from scout_agent.models.scout import Scout


class PlatformAdapter(ABC):
    """READ-ONLY AUTOMATION: adapters may navigate and read pages only."""

    platform_name: str
    scout_list_url: str | None = None

    @abstractmethod
    def is_logged_in(self, page: Page | None) -> bool: ...

    @abstractmethod
    def get_scout_list(self, page: Page | None) -> list[Any]: ...

    @abstractmethod
    def get_scout_detail(self, page: Page | None, scout: Any) -> Any: ...

    @abstractmethod
    def normalize_scout(self, raw: Any) -> Scout: ...
