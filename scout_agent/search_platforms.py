"""Search contract, independent of Scout-message adapters.

Only observed and verified platform implementations may be enabled by the CLI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from scout_agent.platforms.green import JOB_PATH

ORIGIN = 'https://www.green-japan.com'
FIELDS = ('company', 'title', 'salary', 'location', 'remote', 'responsibilities', 'required', 'preferred', 'technology')


def job_identity(platform: str, external_id: str) -> str:
    """Green's historical numeric pair remains unchanged; others are namespaced."""
    if not re.fullmatch(r'[a-z][a-z0-9_]*', platform) or not re.fullmatch(r'[A-Za-z0-9_:-]+', external_id):
        raise ValueError('职位身份无效')
    if platform == 'green':
        if not re.fullmatch(r'[0-9]+:[0-9]+', external_id):
            raise ValueError('Green 职位身份无效')
        return external_id
    return f'{platform}:{external_id}'


def source_identity(platform: str, source: str) -> str:
    if not re.fullmatch(r'[a-z][a-z0-9_]*', platform) or not source or ':' in source:
        raise ValueError('搜索来源身份无效')
    return source if platform == 'green' else f'{platform}:{source}'


@dataclass
class Job:
    job_id: str
    url: str
    fields: dict[str, str]
    matched_keywords: list[str] = field(default_factory=list)

    platform: str | None = None
    external_job_id: str | None = None

    @classmethod
    def from_url(cls, url, fields):
        if not isinstance(url, str) or not url or re.search(r"[\s\x00-\x1f\x7f]", url):
            raise ValueError("异常 Green 职位 URL，已安全停止。")
        parts = urlsplit(urljoin(ORIGIN, url))
        if parts.scheme != 'https' or parts.netloc != 'www.green-japan.com' or not JOB_PATH.fullmatch(parts.path):
            raise ValueError('非 Green 职位 URL，已安全停止。')
        ids = parts.path.split('/')
        identity = f'{ids[2]}:{ids[4]}'
        return cls(identity, ORIGIN + parts.path, fields, platform='green', external_job_id=identity)



class SearchAdapter(Protocol):
    platform_key: str
    source_labels: tuple[str, ...]

    def ensure_verified(self) -> None: ...
    def source_url(self, label: str, page: int = 1) -> str: ...
    def validate_job(self, job: Job) -> str: ...
    def search_cards(self, keyword: str, page: int) -> list[Job]: ...
    def job_detail(self, job: Job) -> dict[str, str]: ...
