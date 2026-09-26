from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Iterator, Literal
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright


class CDPConnectionError(RuntimeError):
    """The configured Chrome debugging endpoint cannot be used."""


@dataclass(frozen=True)
class BrowserSession:
    mode: Literal["cdp", "persistent"]
    contexts: tuple[BrowserContext, ...]

    @property
    def pages(self) -> tuple[Page, ...]:
        return tuple(page for context in self.contexts for page in context.pages)


class BrowserManager:
    """READ-ONLY AUTOMATION: attach to Chrome or launch the legacy profile."""

    def __init__(
        self,
        profile_path: Path,
        *,
        mode: Literal["cdp", "persistent"] = "cdp",
        cdp_endpoint: str = "http://127.0.0.1:9222",
        headless: bool = False,
    ) -> None:
        if mode not in {"cdp", "persistent"}:
            raise ValueError("BROWSER_MODE must be 'cdp' or 'persistent'")
        self.profile_path = profile_path
        self.mode = mode
        self.cdp_endpoint = cdp_endpoint
        self.headless = headless

    def _cdp_unavailable(self) -> CDPConnectionError:
        return CDPConnectionError(
            f"Chrome CDP endpoint not available at {self.cdp_endpoint}.\n"
            "Please start/enable remote debugging in your Job Scout Chrome profile."
        )

    def check_cdp_endpoint(self) -> None:
        """Bounded, read-only readiness check before Playwright attaches."""
        try:
            parts = urlsplit(self.cdp_endpoint)
            if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
                raise ValueError("Invalid CDP endpoint URL")
            version_url = urlunsplit((parts.scheme, parts.netloc, "/json/version", "", ""))
            with urlopen(version_url, timeout=2) as response:
                version = json.load(response)
            if not isinstance(version, dict) or not version.get("webSocketDebuggerUrl"):
                raise ValueError("Not a Chrome CDP endpoint")
        except (OSError, ValueError, UnicodeError) as exc:
            raise self._cdp_unavailable() from exc

    @contextmanager
    def open(self) -> Iterator[BrowserSession]:
        if self.mode == "cdp":
            self.check_cdp_endpoint()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.connect_over_cdp(
                        self.cdp_endpoint,
                        timeout=5000,
                        no_defaults=True,
                    )
                except PlaywrightError as exc:
                    raise self._cdp_unavailable() from exc
                # Do not close Browser or its existing contexts/pages. Stopping
                # Playwright disconnects this client and leaves user's Chrome open.
                yield BrowserSession(mode="cdp", contexts=tuple(browser.contexts))
            return

        self.profile_path.mkdir(parents=True, exist_ok=True)
        browser_env = os.environ.copy()
        local_libs = Path(sys.prefix) / "chromium-deps" / "usr" / "lib" / "x86_64-linux-gnu"
        if local_libs.is_dir():
            browser_env["LD_LIBRARY_PATH"] = os.pathsep.join(
                filter(None, (str(local_libs), browser_env.get("LD_LIBRARY_PATH")))
            )
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_path,
                headless=self.headless,
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                env=browser_env,
            )
            try:
                yield BrowserSession(mode="persistent", contexts=(context,))
            finally:
                context.close()

    @staticmethod
    def open_page(context: BrowserContext, url: str | None = None) -> Page:
        """Navigation helper for future read-only adapters; never used by CDP browser command."""
        page = context.pages[0] if context.pages else context.new_page()
        if url:
            page.goto(url)
        return page
