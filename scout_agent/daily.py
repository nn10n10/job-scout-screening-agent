"""One-shot orchestration over the four implemented read-only Scout scanners."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sys

from scout_agent.config import Settings
from scout_agent.llm.codex import CodexClassifierError
from scout_agent.local_reprocess import reprocess_stored_scout
from scout_agent.models.evaluation import Evaluation
from scout_agent.models.scout import Scout
from scout_agent.report.generator import generate_daily_report
from scout_agent.storage.db import Database


DAILY_PLATFORMS = ("green", "type", "doda", "mynavi")


def normalize_platforms(platforms):
    if not isinstance(platforms, (list, tuple)) or not platforms:
        raise ValueError("请选择至少一个平台")
    if any(not isinstance(p, str) or p not in DAILY_PLATFORMS for p in platforms):
        raise ValueError("平台无效")
    return tuple(p for p in DAILY_PLATFORMS if p in platforms)


def _new_ids(db: Database, platform: str, after_id: int) -> set[int]:
    return {
        int(row["id"]) for row in db.conn.execute(
            "SELECT id FROM scouts WHERE platform=? AND id>?", (platform, after_id),
        )
    }


def _pending(db: Database, platforms=DAILY_PLATFORMS) -> list[tuple[int, Scout]]:
    # This includes a previous daily's fully captured but unevaluated details.
    # Existing evaluations, including historical paid results, are never selected.
    return [
        item for platform in platforms
        for item in db.get_scouts_for_evaluation(
            platform=platform, eligible_only=platform in {"doda", "mynavi"},
        )
        if item[1].scout_text or item[1].jd_text
    ]


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, CodexClassifierError):
        return f"{exc.category}: {exc}"
    return f"{type(exc).__name__}; pending records retained for retry"


def _pending_count(db: Database, platforms=DAILY_PLATFORMS) -> int:
    count = len(_pending(db, platforms))
    for platform in platforms:
        if platform == "green":
            continue
        table = f"{platform}_list_items"
        if db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
        ).fetchone() is None:
            continue
        count += int(db.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE status='pending'"
        ).fetchone()[0])
    return count


def run_daily(
    settings: Settings, *, dry_run: bool,
    scan_platform: Callable[[str, dict], int],
    classifier_factory: Callable[[Settings], object],
    finalize_evaluation: Callable[[Evaluation, Scout], Evaluation],
    platforms=None, progress=None,
) -> int:
    platforms = normalize_platforms(DAILY_PLATFORMS if platforms is None else platforms)

    def notify(platform, status):
        if progress:
            progress({"platform": platform, "status": status})

    if settings.browser_mode != "cdp":
        print("daily requires BROWSER_MODE=cdp; it never launches Chromium.", file=sys.stderr)
        return 2
    if dry_run:
        count = 0
        if settings.db_path.exists():
            with Database(settings.db_path, read_only=True) as db:
                count = _pending_count(db, platforms)
        print("Daily dry-run: " + " -> ".join(platforms))
        print(f"Classifier provider: {settings.classifier_provider}")
        print(f"Stored classifier pending: {count}")
        print("No browser, model, evaluation, database write, or report generation.")
        return 0

    with Database(settings.db_path) as db:
        daily_run_id = db.start_run("daily")
        summaries: dict[str, dict] = {}
        new_ids: set[int] = set()
        errors: dict[str, str] = {}
        for platform in platforms:
            notify(platform, "running")
            before_id = int(db.conn.execute("SELECT COALESCE(MAX(id),0) FROM scouts").fetchone()[0])
            capture: dict = {}
            try:
                exit_code = scan_platform(platform, capture)
                if exit_code != 0:
                    errors[platform] = f"scan exited with code {exit_code}; pending records retained"
                elif capture.get("stats", {}).get("detail_failed", 0):
                    failed = capture["stats"]["detail_failed"]
                    errors[platform] = f"{failed} detail fetch(es) failed; pending records retained"
            except Exception as exc:
                errors[platform] = _safe_error(exc)
            platform_new = _new_ids(db, platform, before_id)
            new_ids.update(platform_new)
            stats = capture.get("stats", {})
            newly_title_skipped = stats.get("new_title_skipped", 0)
            summaries[platform] = {
                "list_scanned": stats.get("list_scanned", 0),
                "new": len(platform_new) + newly_title_skipped,
                "already_seen": stats.get("already_seen", 0),
                "local_skip": newly_title_skipped,
                "sent_to_classifier": 0,
                "KEEP": 0, "MAYBE": 0,
                "SKIP": newly_title_skipped,
                "error": errors.get(platform),
            }
            print(
                f"Daily {platform}: list_scanned={summaries[platform]['list_scanned']} "
                f"new={summaries[platform]['new']} already_seen={summaries[platform]['already_seen']} "
                f"error={errors.get(platform) or 'none'}",
                flush=True,
            )

            notify(platform, "waiting")

        # Repair an interrupted scan that saved full details before writing its
        # local decision. Only unevaluated doda/mynavi Scouts are touched.
        local_processed_ids: set[int] = set()
        for platform in platforms:
            if platform not in {"doda", "mynavi"}:
                continue
            notify(platform, "running")
            for scout_id, scout in db.get_scouts_for_evaluation(platform=platform):
                if not scout.jd_text:
                    continue
                try:
                    local = reprocess_stored_scout(scout)
                    db.save_local_reprocess_result(scout_id, platform, daily_run_id, local)
                    if local.decision == "local_skip":
                        local_processed_ids.add(scout_id)
                except Exception as exc:
                    errors[f"{platform}:local:{scout_id}"] = _safe_error(exc)

            notify(platform, "waiting")

        pending = _pending(db, platforms)
        classified_ids: set[int] = set()
        classifier_error: str | None = None
        model_name = "none"
        if pending:
            try:
                classifier = classifier_factory(settings)
                model_name = classifier.model_name
                batch_size = settings.codex_batch_size if classifier.provider == "codex" else 1
                for platform in platforms:
                    platform_pending = [item for item in pending if item[1].platform == platform]
                    for offset in range(0, len(platform_pending), batch_size):
                        batch = platform_pending[offset:offset + batch_size]
                        notify(batch[0][1].platform, "running")
                        by_id = {str(scout_id): (scout_id, scout) for scout_id, scout in batch}
                        if len(batch) > 1:
                            evaluations = classifier.classify_many(
                                [(str(scout_id), scout) for scout_id, scout in batch]
                            )
                        else:
                            scout_id, scout = batch[0]
                            evaluations = {str(scout_id): classifier.classify(scout)}
                        if not isinstance(evaluations, dict) or set(evaluations) - set(by_id):
                            raise ValueError("Classifier returned unexpected Scout IDs")
                        for internal_id, (scout_id, scout) in by_id.items():
                            if internal_id not in evaluations:
                                continue
                            evaluation = finalize_evaluation(evaluations[internal_id], scout)
                            if db.save_evaluation_if_absent(
                                scout_id, daily_run_id, evaluation,
                                provider=classifier.provider, model_name=classifier.model_name,
                            ):
                                classified_ids.add(scout_id)
                                summaries[scout.platform]["sent_to_classifier"] += 1
                        if len(evaluations) < len(batch):
                            raise ValueError("Classifier returned an incomplete batch; missing records remain pending")
                    if platform_pending:
                        notify(platform, "waiting")
            except Exception as exc:
                classifier_error = _safe_error(exc)
                errors["classifier"] = classifier_error

        for platform in platforms:
            failed = platform in errors or any(key.startswith(platform + ":") for key in errors)
            failed = failed or (classifier_error is not None and any(
                scout.platform == platform and scout_id not in classified_ids for scout_id, scout in pending
            ))
            notify(platform, "failed" if failed else "completed")

        report_ids = new_ids | classified_ids | local_processed_ids
        results = db.get_results_for_scout_ids(report_ids)
        new_local = db.get_results_for_scout_ids(new_ids | local_processed_ids)
        for scout, _, meta in new_local:
            if meta.provider == "local":
                summaries[scout.platform]["local_skip"] += 1
        for scout, evaluation, _ in results:
            summaries[scout.platform][evaluation.verdict] += 1
        totals = {key: sum(row[key] for row in summaries.values()) for key in (
            "list_scanned", "new", "already_seen", "local_skip", "sent_to_classifier",
            "KEEP", "MAYBE", "SKIP",
        )}
        pending_remaining = _pending_count(db, platforms)
        db.finish_run(daily_run_id, error="partial failure" if errors else None)
        html, json_path = generate_daily_report(
            settings.output_path, platform_stats=summaries, totals=totals,
            results=results, errors=errors, model_name=model_name,
            pending_remaining=pending_remaining,
            generated_at=datetime.now(),
        )
        print(f"Daily classifier: evaluated={len(classified_ids)} pending={pending_remaining}")
        print(f"Daily verdicts: KEEP={totals['KEEP']} MAYBE={totals['MAYBE']} SKIP={totals['SKIP']}")
        print(f"Daily errors: {errors or 'none'}")
        print(f"Daily HTML report: {html}")
        print(f"Daily JSON report: {json_path}")
        return 1 if errors else 0
