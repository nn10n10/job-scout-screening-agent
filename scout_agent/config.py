from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path
    gemini_api_key: str | None
    gemini_model: str | None
    browser_mode: Literal["cdp", "persistent"] = "cdp"
    cdp_endpoint: str = "http://127.0.0.1:9222"
    classifier_provider: Literal["mock", "codex", "gemini"] = "mock"
    codex_model: str | None = None
    codex_reasoning_effort: str | None = "low"
    codex_batch_size: int = 8
    list_scan_limit: int = 100
    scout_max_age_days: int = 14
    seen_stop_threshold: int = 30

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "scouts.db"

    @property
    def profile_path(self) -> Path:
        return self.root / "browser_profiles" / "scout"

    @property
    def output_path(self) -> Path:
        return self.root / "output"

    @property
    def rules_path(self) -> Path:
        return Path(__file__).parent / "prompts" / "screening_rules.txt"


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    load_dotenv(root / ".env", override=False)
    browser_mode = (os.getenv("BROWSER_MODE") or "cdp").strip().lower()
    if browser_mode not in {"cdp", "persistent"}:
        raise ValueError("BROWSER_MODE must be 'cdp' or 'persistent'")
    classifier_provider = (os.getenv("CLASSIFIER_PROVIDER") or "mock").strip().lower()
    if classifier_provider not in {"mock", "codex", "gemini"}:
        raise ValueError("CLASSIFIER_PROVIDER must be 'mock', 'codex', or 'gemini'")
    try:
        codex_batch_size = int(os.getenv("CODEX_BATCH_SIZE", "8"))
    except ValueError as exc:
        raise ValueError("CODEX_BATCH_SIZE must be a positive integer") from exc
    if codex_batch_size < 1:
        raise ValueError("CODEX_BATCH_SIZE must be a positive integer")
    def positive_setting(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value
    return Settings(
        root=root,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL") or None,
        browser_mode=browser_mode,
        cdp_endpoint=(os.getenv("CDP_ENDPOINT") or "http://127.0.0.1:9222").strip(),
        classifier_provider=classifier_provider,
        codex_model=(os.getenv("CODEX_MODEL") or "").strip() or None,
        codex_reasoning_effort=(os.getenv("CODEX_REASONING_EFFORT", "low")).strip() or None,
        codex_batch_size=codex_batch_size,
        list_scan_limit=positive_setting("LIST_SCAN_LIMIT", 100),
        scout_max_age_days=positive_setting("SCOUT_MAX_AGE_DAYS", 14),
        seen_stop_threshold=positive_setting("SEEN_STOP_THRESHOLD", 30),
    )
