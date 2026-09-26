from datetime import timedelta

import pytest

from scout_agent.platforms.green import (
    GreenAdapter,
    parse_job_card,
    parse_received_at,
    parse_scout_ref,
)


def test_parse_observed_thread_link_with_fictional_card():
    ref = parse_scout_ref(
        "/messages/v2/[threadId]?threadId=12345678",
        "スカウト\n架空クラウド株式会社\n架空の案内文\n架空の求人\n500万円〜",
    )
    assert ref.external_id == "12345678"
    assert ref.company_name == "架空クラウド株式会社"
    assert ref.url == "https://www.green-japan.com/messages/v2/12345678?threadId=12345678"


@pytest.mark.parametrize("href", [
    "/messages/v2/[threadId]",
    "/messages/v2/[threadId]?threadId=not-a-number",
    "https://example.com/messages/v2/[threadId]?threadId=12345678",
])
def test_rejects_links_without_valid_green_thread_id(href):
    with pytest.raises(ValueError):
        parse_scout_ref(href, "スカウト\n架空社")


def test_parse_detail_fields_conservatively():
    title, salary, location = parse_job_card(
        ["架空の AWS エンジニア", "500万円〜700万円", "東京都・リモート可"]
    )
    assert (title, salary, location) == (
        "架空の AWS エンジニア", "500万円〜700万円", "東京都・リモート可"
    )
    assert parse_job_card(["架空の職種", "応相談", "未記載"]) == (
        "架空の職種", None, None
    )
    received = parse_received_at("2026/09/20 14:30")
    assert received is not None
    assert received.utcoffset() == timedelta(hours=9)
    assert parse_received_at("昨日") is None


def test_normalize_preserves_unknown_fields_as_none():
    scout = GreenAdapter().normalize_scout({
        "id": "12345678",
        "platform": "green",
        "company_name": "架空社",
        "url": "https://www.green-japan.com/messages/v2/12345678?threadId=12345678",
    })
    assert scout.id == "12345678"
    assert scout.scout_title is None
    assert scout.jd_text is None
    assert scout.received_at is None
