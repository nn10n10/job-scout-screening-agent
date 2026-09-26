from datetime import datetime

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
