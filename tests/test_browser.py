from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest
from playwright.sync_api import Error as PlaywrightError

from scout_agent.browser import manager as browser_module
from scout_agent.browser.manager import BrowserManager, CDPConnectionError
from scout_agent.cli import main
from scout_agent.config import Settings, load_settings


def test_default_mode_and_environment_override(monkeypatch, tmp_path):
    monkeypatch.delenv("BROWSER_MODE", raising=False)
    monkeypatch.delenv("CDP_ENDPOINT", raising=False)
    settings = load_settings(tmp_path)
    assert settings.browser_mode == "cdp"
    assert settings.cdp_endpoint == "http://127.0.0.1:9222"
    monkeypatch.setenv("BROWSER_MODE", "persistent")
    assert load_settings(tmp_path).browser_mode == "persistent"


def test_cdp_unavailable_never_launches_chromium(monkeypatch, tmp_path):
    def fail_request(*_args, **_kwargs):
        raise URLError("connection refused")

    playwright_factory = MagicMock()
    monkeypatch.setattr(browser_module, "urlopen", fail_request)
    monkeypatch.setattr(browser_module, "sync_playwright", playwright_factory)
    manager = BrowserManager(tmp_path / "profile", mode="cdp")
    with pytest.raises(CDPConnectionError, match="Chrome CDP endpoint not available"):
        with manager.open():
            pass
    playwright_factory.assert_not_called()
    assert not manager.profile_path.exists()


def test_cdp_attach_reads_existing_tabs_and_does_not_close_chrome(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser_module,
        "urlopen",
        lambda *_args, **_kwargs: BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/test"}'),
    )
    page = SimpleNamespace(title=lambda: "Existing tab", url="https://example.com/")
    context = SimpleNamespace(pages=[page], close=MagicMock(), new_page=MagicMock())
    browser = SimpleNamespace(contexts=[context], close=MagicMock())
    chromium = SimpleNamespace(connect_over_cdp=MagicMock(return_value=browser),
                               launch_persistent_context=MagicMock())

    @contextmanager
    def fake_playwright():
        yield SimpleNamespace(chromium=chromium)

    monkeypatch.setattr(browser_module, "sync_playwright", fake_playwright)
    with BrowserManager(tmp_path / "profile", mode="cdp").open() as session:
        assert session.mode == "cdp"
        assert len(session.contexts) == 1
        assert session.pages == (page,)

    chromium.connect_over_cdp.assert_called_once_with(
        "http://127.0.0.1:9222", timeout=5000, no_defaults=True
    )
    chromium.launch_persistent_context.assert_not_called()
    context.new_page.assert_not_called()
    context.close.assert_not_called()
    browser.close.assert_not_called()


def test_cdp_attach_failure_never_falls_back_to_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser_module,
        "urlopen",
        lambda *_args, **_kwargs: BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/test"}'),
    )
    chromium = SimpleNamespace(
        connect_over_cdp=MagicMock(side_effect=PlaywrightError("connection lost")),
        launch_persistent_context=MagicMock(),
    )

    @contextmanager
    def fake_playwright():
        yield SimpleNamespace(chromium=chromium)

    monkeypatch.setattr(browser_module, "sync_playwright", fake_playwright)
    with pytest.raises(CDPConnectionError, match="Chrome CDP endpoint not available"):
        with BrowserManager(tmp_path / "profile", mode="cdp").open():
            pass
    chromium.launch_persistent_context.assert_not_called()


def test_browser_command_lists_existing_tabs_without_navigation(monkeypatch, tmp_path, capsys):
    page = SimpleNamespace(title=lambda: "求人", url="https://example.com/scout")
    context = SimpleNamespace(pages=[page], new_page=MagicMock(), close=MagicMock())
    session = SimpleNamespace(contexts=(context,), pages=(page,))

    @contextmanager
    def fake_open(_self):
        yield session

    monkeypatch.setattr("scout_agent.cli.load_settings", lambda: Settings(tmp_path, None, None))
    monkeypatch.setattr(BrowserManager, "open", fake_open)
    monkeypatch.setattr("scout_agent.cli.time.sleep", lambda _seconds: None)
    assert main(["browser"]) == 0
    output = capsys.readouterr().out
    assert "求人 | https://example.com/scout" in output
    assert "Chrome remains open" in output
    context.new_page.assert_not_called()
    context.close.assert_not_called()


def test_browser_command_reports_unavailable_cdp(monkeypatch, tmp_path, capsys):
    @contextmanager
    def unavailable(_self):
        raise CDPConnectionError("Chrome CDP endpoint not available")
        yield  # pragma: no cover

    monkeypatch.setattr("scout_agent.cli.load_settings", lambda: Settings(tmp_path, None, None))
    monkeypatch.setattr(BrowserManager, "open", unavailable)
    assert main(["browser"]) == 1
    assert "Chrome CDP endpoint not available" in capsys.readouterr().err


def test_status_reports_cdp_counts(monkeypatch, tmp_path, capsys):
    session = SimpleNamespace(contexts=(object(), object()), pages=(object(),))

    @contextmanager
    def fake_open(_self):
        yield session

    monkeypatch.setattr("scout_agent.cli.load_settings", lambda: Settings(tmp_path, None, None))
    monkeypatch.setattr(BrowserManager, "open", fake_open)
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "Browser mode: cdp" in output
    assert "CDP endpoint: http://127.0.0.1:9222" in output
    assert "Chrome reachable: yes" in output
    assert "Chrome contexts: 2" in output
    assert "Chrome pages: 1" in output
    assert "DB: OK" in output
    assert "Current classifier: Mock" in output


def test_browser_command_persistent_keeps_legacy_path(monkeypatch, tmp_path, capsys):
    context = SimpleNamespace(pages=[])
    session = SimpleNamespace(contexts=(context,))

    @contextmanager
    def fake_open(_self):
        yield session

    open_page = MagicMock()
    monkeypatch.setattr(
        "scout_agent.cli.load_settings",
        lambda: Settings(tmp_path, None, None, browser_mode="persistent"),
    )
    monkeypatch.setattr(BrowserManager, "open", fake_open)
    monkeypatch.setattr(BrowserManager, "open_page", open_page)
    monkeypatch.setattr("builtins.input", lambda: "")
    assert main(["browser"]) == 0
    open_page.assert_called_once_with(context)
    assert "legacy persistent Chromium" in capsys.readouterr().out


def test_persistent_mode_retains_legacy_launch_and_close(monkeypatch, tmp_path):
    context = SimpleNamespace(pages=[], close=MagicMock())
    chromium = SimpleNamespace(launch_persistent_context=MagicMock(return_value=context),
                               connect_over_cdp=MagicMock())

    @contextmanager
    def fake_playwright():
        yield SimpleNamespace(chromium=chromium)

    monkeypatch.setattr(browser_module, "sync_playwright", fake_playwright)
    profile = tmp_path / "profile"
    with BrowserManager(profile, mode="persistent").open() as session:
        assert session.mode == "persistent"
        assert session.contexts == (context,)
    launch_kwargs = chromium.launch_persistent_context.call_args.kwargs
    assert launch_kwargs["user_data_dir"] == profile
    assert launch_kwargs["headless"] is False
    assert launch_kwargs["locale"] == "ja-JP"
    assert launch_kwargs["timezone_id"] == "Asia/Tokyo"
    context.close.assert_called_once_with()
    chromium.connect_over_cdp.assert_not_called()
