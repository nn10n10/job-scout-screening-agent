from datetime import date, datetime

from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.report.generator import generate_report


def test_report_orders_cards_and_escapes_html(tmp_path):
    skip = (Scout(platform="generic", job_title="<script>alert(1)</script>", url="javascript:alert(1)"),
            Evaluation(verdict="SKIP", confidence=0.5, summary="skip"))
    keep = (Scout(platform="generic", company_name="Example", job_title="AWS Engineer",
                  url="https://example.com/scout"),
            Evaluation(verdict="KEEP", confidence=0.7, summary="keep"))
    html_path, json_path = generate_report(tmp_path, [skip, keep], generated_at=datetime(2026, 9, 25, 12, 34))
    html = html_path.read_text(encoding="utf-8")
    assert html.index("AWS Engineer") < html.index("&lt;script&gt;")
    assert "https://example.com/scout" in html
    assert "href=\"javascript:" not in html
    assert "新增 Scout：2" in html
    assert json_path.exists()
    next_html, _ = generate_report(tmp_path, [skip, keep], generated_at=datetime(2026, 9, 25, 12, 34))
    assert next_html != html_path
    assert html_path.exists()


def test_type_report_shows_kind_and_date_only_without_inventing_time(tmp_path):
    scout = Scout(
        platform="type", id="offer_dm:12345678", scout_kind="offer_dm",
        received_on=date(2026, 9, 26), received_at=None,
        company_name="架空社", job_title="Cloud Engineer",
    )
    evaluation = Evaluation(verdict="MAYBE", confidence=0.5, summary="需要人工确认职责。")
    html_path, _ = generate_report(tmp_path, [(scout, evaluation)])
    html = html_path.read_text(encoding="utf-8")
    assert "type（offer_dm）" in html
    assert "收到日期：2026-09-26" in html
    assert "2026-09-26 00:00" not in html


def test_type_report_includes_two_stage_scan_stats(tmp_path):
    evaluation = Evaluation(verdict="KEEP", confidence=0.6, summary="值得人工查看。")
    stats = {
        "list_scanned": 12, "already_seen": 4, "title_skipped": 3,
        "detail_fetched": 5, "locally_skipped": 1, "sent_to_classifier": 4,
        "TITLE_SKIP": 3, "TITLE_REVIEW": 2, "DETAIL": 7,
    }
    html_path, json_path = generate_report(
        tmp_path, [(Scout(platform="type", job_title="Cloud Engineer"), evaluation)],
        scan_stats=stats,
    )
    html = html_path.read_text(encoding="utf-8")
    assert "列表扫描：12" in html
    assert "TITLE_SKIP：3" in html
    assert "TITLE_REVIEW：2" in html
    assert "DETAIL：7" in html
    assert "送入分类器：4" in html
    import json
    assert json.loads(json_path.read_text(encoding="utf-8"))["scan_stats"] == {
        **stats, "KEEP": 1, "MAYBE": 0, "SKIP": 0,
    }
