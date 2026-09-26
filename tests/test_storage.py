from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.storage.db import Database


def test_dedupe_and_run_results(tmp_path):
    scout = Scout(id="external-1", platform="generic", company_name="架空会社")
    evaluation = Evaluation(verdict="MAYBE", confidence=0.4, summary="信息不足")
    with Database(tmp_path / "scouts.db") as db:
        run_id = db.start_run("generic")
        assert not db.is_seen(scout)
        scout_id = db.save_scout(scout)
        assert db.save_scout(scout) == scout_id
        assert len(db.get_new_scouts()) == 1
        db.save_evaluation(scout_id, run_id, evaluation)
        db.finish_run(run_id)
        assert db.is_seen(scout)
        assert db.get_new_scouts() == []
        run, results = db.get_recent_results()
        assert run["id"] == run_id
        assert results[0][0].company_name == "架空会社"
        assert results[0][1].verdict == "MAYBE"
        assert db.count_processed() == 1


def test_fallback_keys():
    url_scout = Scout(platform="generic", url="https://example.com/one")
    assert url_scout.dedupe_key == "url:https://example.com/one"
    first = Scout(platform="generic", scout_text="hello")
    second = Scout(platform="generic", scout_text="hello")
    assert first.dedupe_key == second.dedupe_key
    assert first.dedupe_key.startswith("sha256:")
