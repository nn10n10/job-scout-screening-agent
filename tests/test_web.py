from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from scout_agent import cli
from scout_agent.config import Settings
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database
from scout_agent.web.app import create_app
from scout_agent.web.viewmodels import _safe_detail_url


def _seed_dashboard(tmp_path):
    db_path = tmp_path / "data" / "scouts.db"
    now = datetime.now()
    ids = {}
    with Database(db_path) as db:
        run_id = db.start_run("fictional-web-test")
        examples = [
            ("infra", "type", "KEEP", "架空Infra社", "Cloud Engineer", "codex", 1 / 24,
             "https://example.invalid/infra", "年収500万円", "東京", True),
            ("web", "type", "SKIP", "架空Web社", "Web Engineer", "local", 1,
             "javascript:alert(1)", None, None, None),
            ("platform", "doda", "MAYBE", "架空Platform社", "Platform Engineer", "mock", 2,
             None, None, "大阪", None),
            ("sre", "mynavi", "KEEP", "<script>架空社</script>", "SRE", "codex", 3 / 24,
             "https://example.invalid/job?x=1&y=2", None, None, None),
            ("old", "green", "KEEP", "架空Old社", "Legacy Cloud", "codex", 20,
             "https://[invalid/job", None, None, None),
        ]
        for external_id, platform, verdict, company, title, provider, age_days, url, salary, location, remote in examples:
            ids[external_id] = db.save_scout(Scout(
                id=external_id, platform=platform, company_name=company,
                job_title=title, salary_text=salary, location_text=location,
                url=url, scout_text="FICTIONAL PRIVATE SCOUT MESSAGE",
                jd_text=("Webアプリケーション開発が主な業務。" if external_id == "web"
                         else "jobContentOutline: SRE として SLO の改善と監視基盤の自動化を担当。"
                         if external_id == "sre" else
                         "AWS 基盤の設計・構築を担当。自社内勤務。 FICTIONAL PRIVATE JOB DESCRIPTION"),
                received_on=(now - timedelta(days=age_days)).date(),
            ))
            db.save_evaluation(
                ids[external_id], run_id,
                Evaluation(
                    verdict=verdict, confidence=0.82,
                    summary=f"虚构{title}岗位，值得根据现有证据判断。",
                    reasons=["AWS 基盘职责明确。"],
                    concerns=["工作方式待确认。", "薪资范围待确认。", "第三项仅在详情页。"],
                    remote=remote,
                ),
                provider=provider, model_name=f"fictional-{provider}",
                evaluated_at=now - timedelta(days=age_days),
            )
        db.finish_run(run_id)
    return db_path, ids


def test_web_empty_state_does_not_create_database(tmp_path):
    db_path = tmp_path / "data" / "scouts.db"
    with TestClient(create_app(db_path)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "没有符合条件的评价" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.post("/").status_code == 405
        assert client.get("/jobs/1").status_code == 404
    assert not db_path.exists()


def test_web_default_shows_recent_keep_maybe_not_skip_and_stays_read_only(tmp_path):
    db_path, ids = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert html.index("Cloud Engineer") < html.index("SRE")
    assert "Platform Engineer" not in html  # Default excludes Mock evaluations.
    assert "Web Engineer" not in html  # Default excludes SKIP.
    assert "Legacy Cloud" not in html  # Default excludes Scouts received over seven days ago.
    assert "年収 500 万円" in html and "东京" not in html and "東京" in html
    assert "远程：不明" in html  # No JD frequency; old classifier bool is not tier evidence.
    assert "codex" in html and "测试/非正式评价" not in html
    assert 'value="Final" selected' in html
    assert "虚构Cloud Engineer岗位" in html
    assert "收到：" in html and "平台：type" in html and "分类：codex" in html
    assert "工作方式待确认。" in html and "薪资范围待确认。" in html
    assert "第三项仅在详情页。" not in html
    assert f'href="http://testserver/jobs/{ids["infra"]}"' in html
    assert 'href="https://example.invalid/job?x=1&amp;y=2"' in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html
    assert "&lt;script&gt;架空社&lt;/script&gt;" in html
    assert "FICTIONAL PRIVATE SCOUT MESSAGE" not in html
    assert "FICTIONAL PRIVATE JOB DESCRIPTION" not in html
    assert str(db_path) not in html
    with Database(db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 5


def test_web_final_hides_mandatory_casual_but_explicit_provider_keeps_history(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with Database(db_path) as db:
        run_id = db.start_run("fictional-workflow-test")
        mandatory_id = db.save_scout(Scout(
            id="mandatory-casual", platform="green", company_name="架空必经社",
            job_title="Mandatory Cloud Engineer",
            jd_text="AWS 基盤の設計・構築を主担当。\n選考プロセス\n"
                    "カジュアル面談⇒適性検査⇒面接",
            received_on=datetime.now().date(),
        ))
        optional_id = db.save_scout(Scout(
            id="optional-casual", platform="green", company_name="架空可选社",
            job_title="Optional Cloud Engineer",
            jd_text="AWS 基盤の設計・構築を主担当。カジュアル面談歓迎。",
            received_on=datetime.now().date(),
        ))
        for scout_id in (mandatory_id, optional_id):
            db.save_evaluation(
                scout_id, run_id,
                Evaluation(verdict="KEEP", confidence=0.9, summary="虚构云基盘岗位。"),
                provider="codex", model_name="fictional-codex",
            )
        db.finish_run(run_id)
    with TestClient(create_app(db_path)) as client:
        final = client.get("/")
        assert final.status_code == 200
        assert "Mandatory Cloud Engineer" not in final.text
        assert "Optional Cloud Engineer" in final.text
        assert '<strong id="summary-total">3</strong>' in final.text
        history = client.get("/?provider=codex")
        assert history.status_code == 200
        assert "Mandatory Cloud Engineer" in history.text
        assert client.get(f"/jobs/{mandatory_id}").status_code == 200
    with Database(db_path, read_only=True) as db:
        row = db.conn.execute(
            "SELECT provider,json_extract(payload,'$.verdict') AS verdict "
            "FROM evaluations WHERE scout_id=?", (mandatory_id,),
        ).fetchone()
        assert (row["provider"], row["verdict"]) == ("codex", "KEEP")


def test_web_verdict_and_platform_filters(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        skipped = client.get("/?verdict=SKIP")
        assert "Web Engineer" in skipped.text
        assert "Cloud Engineer" not in skipped.text
        assert 'name="verdict"' in skipped.text and 'value="SKIP" selected' in skipped.text
        all_results = client.get("/?verdict=All&days=all")
        assert "Web Engineer" in all_results.text
        assert "Legacy Cloud" in all_results.text
        assert client.get("/?verdict=All&days=All").status_code == 200
        assert 'value="all" selected' in client.get("/?days=All").text
        type_only = client.get("/?platform=type")
        assert "Cloud Engineer" in type_only.text
        assert "Platform Engineer" not in type_only.text
        assert "SRE" not in type_only.text
        assert "Web Engineer" not in type_only.text


def test_web_keyword_and_provider_filters_are_server_side(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        by_company = client.get("/?q=infra")
        assert "Cloud Engineer" in by_company.text
        assert "Platform Engineer" not in by_company.text
        by_title = client.get("/?q=platform&provider=mock")
        assert "Platform Engineer" in by_title.text
        assert "Cloud Engineer" not in by_title.text
        by_provider = client.get("/?provider=mock")
        assert "Platform Engineer" in by_provider.text
        assert "Cloud Engineer" not in by_provider.text
        assert "测试/非正式评价" in by_provider.text
        literal_wildcard = client.get("/?q=%25")
        assert "Cloud Engineer" not in literal_wildcard.text
        injection = client.get("/", params={"q": "' OR 1=1 --"})
        assert "Cloud Engineer" not in injection.text
        assert client.get("/?platform=invalid").status_code == 422


def test_web_date_filter_and_limit_apply_in_sql(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        one_day = client.get("/?days=1")
        assert "Cloud Engineer" in one_day.text and "SRE" in one_day.text
        assert "Platform Engineer" not in one_day.text
        fourteen = client.get("/?days=14")
        assert "Legacy Cloud" not in fourteen.text
        thirty = client.get("/?days=30")
        assert "Legacy Cloud" in thirty.text

    # The 100-row SQL limit applies after Final tier ranking; an eligible KEEP
    # must remain above untiered local SKIPs even when there are 101 of them.
    with Database(db_path) as db:
        run_id = db.start_run("fictional-limit-test")
        for index in range(101):
            scout_id = db.save_scout(Scout(
                id=f"extra-skip-{index}", platform="type",
                company_name="架空Skip社", job_title=f"Skip {index}",
                received_on=datetime.now().date(),
            ))
            db.save_evaluation(
                scout_id, run_id,
                Evaluation(verdict="SKIP", confidence=0.9, summary="虚构跳过。"),
                provider="local", model_name="fictional-local",
                evaluated_at=datetime.now(),
            )
        db.finish_run(run_id)
    with TestClient(create_app(db_path)) as client:
        assert "Cloud Engineer" in client.get("/").text
        assert "Skip 100" not in client.get("/").text
        all_results = client.get("/?verdict=All&days=all")
        assert "显示 100 条" in all_results.text
        assert "Cloud Engineer" in all_results.text
        assert '<strong id="summary-total">105</strong>' in all_results.text


def test_web_summary_uses_current_sql_filters_and_only_shows_skip_when_requested(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        default = client.get("/")
        assert default.status_code == 200
        assert '<strong id="summary-total">2</strong>' in default.text
        assert '<strong id="summary-keep">2</strong>' in default.text
        assert '<strong id="summary-maybe">0</strong>' in default.text
        assert 'id="summary-skip"' not in default.text
        assert "Final results" in default.text
        assert "Final</span>" in default.text and "最近7天</span>" in default.text
        assert "All platforms</span>" in default.text
        assert "type 1" in default.text and "mynavi 1" in default.text
        assert "green 1" not in default.text and "doda 1" not in default.text

        mock = client.get("/?provider=mock")
        assert '<strong id="summary-total">1</strong>' in mock.text
        assert '<strong id="summary-keep">0</strong>' in mock.text
        assert '<strong id="summary-maybe">1</strong>' in mock.text
        assert "doda 1" in mock.text

        skipped = client.get("/?verdict=SKIP")
        assert '<strong id="summary-total">1</strong>' in skipped.text
        assert '<strong id="summary-skip">1</strong>' in skipped.text
        assert '<strong id="summary-keep">0</strong>' in skipped.text
        assert "type 1" in skipped.text

        all_results = client.get("/?provider=All&verdict=All&days=all")
        assert '<strong id="summary-total">5</strong>' in all_results.text
        assert '<strong id="summary-keep">3</strong>' in all_results.text
        assert '<strong id="summary-maybe">1</strong>' in all_results.text
        assert '<strong id="summary-skip">1</strong>' in all_results.text
        assert "全部时间</span>" in all_results.text

        by_platform = client.get("/?platform=type&provider=All&verdict=All&days=all")
        assert '<strong id="summary-total">2</strong>' in by_platform.text
        assert "type 2" in by_platform.text
        assert "mynavi 1" not in by_platform.text

        keyword = client.get("/?q=platform&provider=All")
        assert '<strong id="summary-total">1</strong>' in keyword.text
        assert "关键词：platform" in keyword.text


def test_web_recent_received_date_not_recent_evaluation_and_excludes_generic(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with Database(db_path) as db:
        run_id = db.start_run("fictional-recent-evaluation")
        old_id = db.save_scout(Scout(
            id="old-revalued", platform="type", company_name="架空历史社",
            job_title="Historical Cloud Engineer",
            jd_text="AWS 基盤の設計・構築を担当。自社内勤務。",
            received_on=(datetime.now() - timedelta(days=40)).date(),
        ))
        db.save_evaluation(old_id, run_id, Evaluation(
            verdict="KEEP", confidence=0.8, summary="虚构历史职位。",
        ), provider="codex", model_name="fictional-codex", evaluated_at=datetime.now())
        generic_id = db.save_scout(Scout(
            id="internal", platform="generic", company_name="架空内部测试社",
            job_title="Internal Fixture", received_on=datetime.now().date(),
        ))
        db.save_evaluation(generic_id, run_id, Evaluation(
            verdict="KEEP", confidence=0.8, summary="虚构内部记录。",
        ), provider="mock", model_name="mock", evaluated_at=datetime.now())
        db.finish_run(run_id)
    with TestClient(create_app(db_path)) as client:
        recent = client.get("/")
        assert "Historical Cloud Engineer" not in recent.text
        assert "Internal Fixture" not in recent.text
        assert "收到时间" in recent.text
        assert "按收到时间从新到旧" in recent.text
        assert "Historical Cloud Engineer" in client.get("/?days=all").text
        assert "Internal Fixture" not in client.get("/?days=all").text
        assert client.get(f"/jobs/{old_id}").status_code == 200


def test_web_doda_age_fallback_and_undated_green_is_conservative(tmp_path):
    db_path = tmp_path / "data" / "scouts.db"
    with Database(db_path) as db:
        run_id = db.start_run("fictional-fallback")
        for external_id, platform, title in (
            ("doda-young", "doda", "Young Doda Cloud"),
            ("doda-old", "doda", "Old Doda Cloud"),
            ("green-unknown", "green", "Unknown Green Cloud"),
        ):
            scout_id = db.save_scout(Scout(
                id=external_id, platform=platform, company_name="架空会社", job_title=title,
            ))
            db.save_evaluation(scout_id, run_id, Evaluation(
                verdict="KEEP", confidence=0.7, summary="虚构基础设施职位。",
            ), provider="mock", model_name="mock", evaluated_at=datetime.now())
        for external_id, age in (("doda-young", 2), ("doda-old", 20)):
            db.save_doda_list_item(
                external_id=external_id, company_name="架空会社", job_title=None,
                age_days=age, url="https://example.invalid/fictional", status="completed",
            )
        db.finish_run(run_id)
    with TestClient(create_app(db_path)) as client:
        recent = client.get("/?provider=All")
        assert "Young Doda Cloud" in recent.text
        assert "Old Doda Cloud" not in recent.text
        assert "Unknown Green Cloud" not in recent.text
        all_results = client.get("/?days=all&provider=All")
        assert all_results.status_code == 200
        assert "Young Doda Cloud" in all_results.text
        assert "Old Doda Cloud" in all_results.text
        assert "Unknown Green Cloud" in all_results.text


def test_web_final_excludes_mock_but_all_and_mock_preserve_history(tmp_path):
    db_path, ids = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        final = client.get("/")
        assert final.status_code == 200
        assert "Platform Engineer" not in final.text
        assert "Cloud Engineer" in final.text
        assert client.get("/?provider=Final").text == final.text

        mock = client.get("/?provider=mock")
        assert mock.status_code == 200
        assert "Platform Engineer" in mock.text
        assert "Cloud Engineer" not in mock.text
        assert "测试/非正式评价" in mock.text
        assert 'value="mock" selected' in mock.text

        all_results = client.get("/?provider=All")
        assert all_results.status_code == 200
        assert "Platform Engineer" in all_results.text
        assert "Cloud Engineer" in all_results.text
        assert "测试/非正式评价" in all_results.text
        assert client.get("/?provider=all").status_code == 200
        assert "Platform Engineer" in client.get("/?provider=all").text

        local_skip = client.get("/?verdict=SKIP&provider=local")
        assert "Web Engineer" in local_skip.text
        assert "测试/非正式评价" not in local_skip.text

        mock_detail = client.get(f"/jobs/{ids['platform']}")
        assert mock_detail.status_code == 200
        assert "测试/非正式评价" in mock_detail.text
        assert "mock / fictional-mock" in mock_detail.text
    with Database(db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 5


def test_web_final_includes_future_real_classifier_provider(tmp_path):
    db_path, _ = _seed_dashboard(tmp_path)
    with Database(db_path) as db:
        run_id = db.start_run("fictional-future-provider")
        scout_id = db.save_scout(Scout(
            id="future-provider", platform="type", company_name="架空Future社",
            job_title="Future Cloud Engineer", received_on=datetime.now().date(),
            jd_text="AWS 基盤の設計・構築を担当。自社内勤務。",
        ))
        db.save_evaluation(scout_id, run_id, Evaluation(
            verdict="MAYBE", confidence=0.7, summary="虚构结果。",
        ), provider="future-llm", model_name="fictional-model", evaluated_at=datetime.now())
        db.finish_run(run_id)
    with TestClient(create_app(db_path)) as client:
        assert "Future Cloud Engineer" in client.get("/").text
        assert "Future Cloud Engineer" not in client.get("/?provider=codex").text


def test_web_final_uses_current_local_rules_without_replacing_historical_codex(tmp_path):
    """An old paid verdict remains auditable but is not a current Final hit."""
    from scout_agent.web.services import DashboardFilters, search_evaluations, summarize_evaluations

    db_path = tmp_path / "fictional-scouts.db"
    examples = (
        ("target", "Cloud Engineer", "AWS 基盤の設計・構築を担当。自社内勤務。", None),
        ("application", "ITエンジニア", "Webアプリケーション開発が主な業務。", None),
        ("client-site", "インフラエンジニア｜還元率83％",
         "AWS 基盤の設計・構築を担当。案件は100％選択制。", "首都圏のプロジェクト先に配属。"),
        ("pool", "インフラエンジニア",
         "AWS 基盤の設計・構築を担当。常時1,200件の案件から希望に合う案件をご紹介。", None),
    )
    ids = {}
    with Database(db_path) as db:
        run_id = db.start_run("fictional-old-rule-evaluations")
        for external_id, title, jd, location in examples:
            scout_id = db.save_scout(Scout(
                id=external_id, platform="type", company_name="架空会社", job_title=title,
                jd_text=jd, location_text=location,
                salary_text="単価連動型、還元率82％。" if external_id == "pool" else None,
                received_on=datetime.now().date(),
            ))
            ids[external_id] = scout_id
            db.save_evaluation(
                scout_id, run_id,
                Evaluation(verdict="MAYBE", confidence=0.7, summary="架空の旧評価。"),
                provider="codex", model_name="fictional-codex",
            )
        db.finish_run(run_id)

    final = DashboardFilters()
    assert [row.scout_id for row in search_evaluations(db_path, final)] == [ids["target"]]
    summary = summarize_evaluations(db_path, final)
    assert (summary.total, summary.keep, summary.maybe) == (1, 0, 1)
    assert {row.scout_id for row in search_evaluations(
        db_path, DashboardFilters(provider="codex"),
    )} == set(ids.values())
    with TestClient(create_app(db_path)) as client:
        for external_id in ("application", "client-site", "pool"):
            assert client.get(f"/jobs/{ids[external_id]}").status_code == 200
    with Database(db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations WHERE provider='codex'").fetchone()[0] == 4
        assert db.conn.execute("SELECT COUNT(*) FROM local_reprocess").fetchone()[0] == 0


def test_web_final_requires_confirmed_target_but_explicit_provider_keeps_audit(tmp_path):
    from scout_agent.web.services import DashboardFilters, search_evaluations, summarize_evaluations

    db_path = tmp_path / "fictional-target-gate.db"
    examples = (
        ("confirmed", "Cloud Engineer", "AWS 基盤の設計・構築を主担当。"),
        ("uncertain", "インフラエンジニア", "クラウド案件約半数。希望によりインフラ配属。"),
        ("rejected", "ネットワークエンジニア", "LAN スイッチとルータを設計・運用。"),
    )
    ids = {}
    with Database(db_path) as db:
        run_id = db.start_run("fictional-target-gate")
        for external_id, title, jd in examples:
            scout_id = db.save_scout(Scout(
                id=external_id, platform="type", company_name="架空会社", job_title=title,
                jd_text=jd, received_on=datetime.now().date(),
            ))
            ids[external_id] = scout_id
            db.save_evaluation(
                scout_id, run_id,
                Evaluation(verdict="KEEP", confidence=0.8, summary="架空の歴史評価。"),
                provider="codex", model_name="fictional-codex",
            )
        db.finish_run(run_id)

    assert [row.scout_id for row in search_evaluations(db_path, DashboardFilters())] == [ids["confirmed"]]
    assert summarize_evaluations(db_path, DashboardFilters()).total == 1
    assert {row.scout_id for row in search_evaluations(
        db_path, DashboardFilters(provider="codex"),
    )} == set(ids.values())
    with TestClient(create_app(db_path)) as client:
        assert "インフラエンジニア" not in client.get("/").text
        assert "インフラエンジニア" in client.get("/?provider=codex").text
        assert client.get(f"/jobs/{ids['uncertain']}").status_code == 200
        assert client.get(f"/jobs/{ids['rejected']}").status_code == 200
    with Database(db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations WHERE provider='codex'").fetchone()[0] == 3


def test_web_priority_tier_filter_sort_salary_ceiling_and_detail_are_read_only(tmp_path):
    from scout_agent.web.services import DashboardFilters, search_evaluations, summarize_evaluations

    db_path = tmp_path / "fictional-priority.db"
    cases = (
        ("c", "C Cloud", "AWS 基盤の設計・構築を主担当。", "年収500万円～650万円", 0),
        ("b", "B Cloud", "AWS 基盤の設計・構築を主担当。週3日リモート勤務。",
         "年収500万円～650万円", 1),
        ("a", "A Cloud", "AWS 基盤の設計・構築を主担当。年間休日125日。",
         "年収500万円～650万円", 2),
        ("s", "S Cloud", "AWS 基盤の設計・構築を主担当。原則フルリモート勤務。",
         "年収500万円～650万円", 3),
        ("low", "Low Cloud", "AWS 基盤の設計・構築を主担当。原則フルリモート勤務。",
         "年収350万円～430万円", 0),
        ("span", "Span Cloud", "AWS 基盤の設計・構築を主担当。",
         "年収400万円～600万円", 1),
    )
    ids = {}
    with Database(db_path) as db:
        run_id = db.start_run("fictional-priority")
        for external_id, title, jd, salary, days_old in cases:
            scout_id = db.save_scout(Scout(
                id=external_id, platform="type", company_name="架空会社", job_title=title,
                jd_text=jd, salary_text=salary,
                received_on=(datetime.now() - timedelta(days=days_old)).date(),
            ))
            ids[external_id] = scout_id
            db.save_evaluation(
                scout_id, run_id,
                Evaluation(verdict="KEEP", confidence=0.8, summary="架空正式评价。"),
                provider="codex", model_name="fictional-codex",
            )
        db.finish_run(run_id)

    final = search_evaluations(db_path, DashboardFilters())
    assert [row.job_title for row in final] == [
        "S Cloud", "A Cloud", "B Cloud", "C Cloud", "Span Cloud",
    ]
    assert summarize_evaluations(db_path, DashboardFilters()).total == 5
    assert [row.scout_id for row in search_evaluations(
        db_path, DashboardFilters(tier="B"),
    )] == [ids["b"]]
    assert {row.scout_id for row in search_evaluations(
        db_path, DashboardFilters(provider="codex", tier="S"),
    )} == {ids["s"], ids["low"]}
    with TestClient(create_app(db_path)) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Tier S" in home.text and "Tier A" in home.text
        assert "年休：125日" in home.text
        assert "薪资：年収 400–600 万円" in home.text
        assert "Low Cloud" not in home.text
        assert "Low Cloud" in client.get("/?provider=codex").text
        tier_b = client.get("/?tier=B")
        assert tier_b.status_code == 200 and "B Cloud" in tier_b.text
        assert "S Cloud" not in tier_b.text
        assert client.get("/?tier=invalid").status_code == 422
        detail = client.get(f"/jobs/{ids['s']}")
        assert detail.status_code == 200
        assert "Priority evidence" in detail.text
        assert "JD 明确：原則フルリモート" in detail.text
    with Database(db_path, read_only=True) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM evaluations WHERE provider='codex'").fetchone()[0] == 6


def test_web_detail_page_uses_stored_evaluation_without_private_scout_text(tmp_path):
    db_path, ids = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        response = client.get(f"/jobs/{ids['infra']}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        html = response.text
        for value in (
            "架空Infra社", "Cloud Engineer", "KEEP", "82%", "AWS 基盘职责明确。",
            "工作方式待确认。", "年収 500 万円", "東京", "Remote",
            "codex / fictional-codex", "评价时间", "https://example.invalid/infra",
        ):
            assert value in html
        assert "第三项仅在详情页。" in html
        assert "FICTIONAL PRIVATE SCOUT MESSAGE" not in html
        assert "FICTIONAL PRIVATE JOB DESCRIPTION" not in html
        assert str(db_path) not in html
        assert client.get("/jobs/999999").status_code == 404


def test_web_unsafe_urls_are_not_clickable_on_card_or_detail(tmp_path):
    db_path, ids = _seed_dashboard(tmp_path)
    with TestClient(create_app(db_path)) as client:
        card = client.get("/?verdict=SKIP").text
        detail = client.get(f"/jobs/{ids['web']}").text
        malformed_card = client.get("/?days=All&q=Legacy").text
        malformed_detail = client.get(f"/jobs/{ids['old']}").text
    assert 'href="javascript:' not in card
    assert 'href="javascript:' not in detail
    assert "链接不可用" in detail
    assert 'href="https://[invalid' not in malformed_card
    assert 'href="https://[invalid' not in malformed_detail
    assert "链接不可用" in malformed_detail
    assert _safe_detail_url("https://[invalid/job") is None
    assert _safe_detail_url("https://user:password@example.invalid/job") is None
    assert _safe_detail_url("https://example.invalid/job?token=fictional-secret") is None


def test_web_cli_binds_only_loopback_without_opening_database(tmp_path, monkeypatch):
    from scout_agent.web import app as web_app

    settings = Settings(tmp_path, None, None)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    seen = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        assert app.state.db_path == settings.db_path

    monkeypatch.setattr(web_app.uvicorn, "run", fake_run)
    assert cli.main(["web"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8765, "access_log": False}
    assert not settings.db_path.exists()
