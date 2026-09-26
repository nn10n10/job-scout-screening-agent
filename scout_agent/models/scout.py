from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, Field


class Scout(BaseModel):
    id: str | None = None  # External scout ID, when available.
    platform: str
    company_name: str | None = None
    job_title: str | None = None
    scout_title: str | None = None
    scout_text: str | None = None
    jd_text: str | None = None
    salary_text: str | None = None
    location_text: str | None = None
    url: str | None = None
    received_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=datetime.now)

    @property
    def dedupe_key(self) -> str:
        if self.id:
            return f"id:{self.id}"
        if self.url:
            return f"url:{self.url}"
        content = self.model_dump(exclude={"id", "url", "scraped_at"}, mode="json")
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f"sha256:{digest}"
