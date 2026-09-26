from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import sys
import time
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError

from scout_agent.browser.manager import BrowserManager, CDPConnectionError
from scout_agent.config import load_settings
from scout_agent.llm.codex import CodexClassifier, CodexClassifierError, codex_auth_mode, codex_cli_available
from scout_agent.llm.base import MISSING_MESSAGE_CONCERN, validate_explanations
from scout_agent.llm.gemini import GeminiClassifier
from scout_agent.llm.mock import MockClassifier
from scout_agent.models.scout import Scout
from scout_agent.models.evaluation import Evaluation
from scout_agent.platforms import ADAPTERS
from scout_agent.platforms.type_jp import prefilter_jobs, prefilter_title, type_post_detail_hard_rule
from scout_agent.report.generator import generate_report
from scout_agent.storage.db import Database


def _classifier(settings):
    if settings.classifier_provider == "mock":
        print("Classifier provider: mock.")
        return MockClassifier()
    if settings.classifier_provider == "codex":
        return CodexClassifier(
            settings.codex_model,
            settings.rules_path.read_text(encoding="utf-8"),
            settings.codex_reasoning_effort,
        )
    if settings.classifier_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for CLASSIFIER_PROVIDER=gemini")
        if not settings.gemini_model:
            raise ValueError("GEMINI_MODEL is required for CLASSIFIER_PROVIDER=gemini")
        return GeminiClassifier(
            settings.gemini_api_key, settings.gemini_model,
            settings.rules_path.read_text(encoding="utf-8"),
        )
    raise ValueError(f"Unsupported classifier provider: {settings.classifier_provider}")


def _configured_model_name(settings):
    if settings.classifier_provider == "codex":
        return settings.codex_model or "codex-default"
    if settings.classifier_provider == "gemini":
        return settings.gemini_model or "unconfigured"
    return "mock"


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _type_list_job_id(external_id: str, index: int) -> str:
    # type's list has message IDs and job titles, but exposes job IDs only on detail links.
    return f"{external_id}:list:{index + 1}"


def _classify(classifier, scout: Scout):
    return _finalize_evaluation(classifier.classify(scout), scout)


def _finalize_evaluation(evaluation, scout: Scout):
    if not scout.scout_text and scout.jd_text and MISSING_MESSAGE_CONCERN not in evaluation.concerns:
        evaluation = evaluation.model_copy(update={
            "concerns": [*evaluation.concerns, MISSING_MESSAGE_CONCERN],
        })
    return validate_explanations(evaluation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="READ-ONLY local Job Scout screening agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("browser", help="Inspect existing Chrome tabs (CDP) or open legacy persistent Chromium")
    scan = sub.add_parser("scan", help="Run one read-only scan")
    scan.add_argument("--platform", required=True, choices=ADAPTERS.keys())
    evaluate = sub.add_parser("evaluate", help="Classify stored Scouts without browser access")
    evaluate.add_argument("--platform", choices=ADAPTERS.keys())
    evaluate.add_argument("--limit", type=_positive_int)
    evaluate.add_argument("--force", action="store_true", help="Replace existing evaluations")
    evaluate.add_argument("--replace-provider", help="Replace only evaluations from this provider")
    evaluate.add_argument("--dry-run", action="store_true", help="Show selection without writing or classifying")
    sub.add_parser("report", help="Regenerate report for latest completed scan")
    sub.add_parser("status", help="Show local setup and database status")
    args = parser.parse_args(argv)
    if args.command == "evaluate" and args.force and args.replace_provider:
        parser.error("--force and --replace-provider cannot be combined")
    settings = load_settings()

    if args.command == "evaluate" and args.dry_run:
        with Database(settings.db_path, read_only=True) as db:
            selected = db.get_scouts_for_evaluation(
                platform=args.platform, limit=args.limit, force=args.force,
                replace_provider=args.replace_provider,
            )
        print(f"Selected Scouts: {len(selected)}")
        return 0

    if args.command == "browser":
        manager = BrowserManager(
            settings.profile_path,
            mode=settings.browser_mode,
            cdp_endpoint=settings.cdp_endpoint,
        )
        if settings.browser_mode == "cdp":
            try:
                with manager.open() as session:
                    print(f"Connected to Chrome at {settings.cdp_endpoint}.")
                    print(f"Contexts: {len(session.contexts)}; pages: {len(session.pages)}")
                    for page in session.pages:
                        try:
                            title = page.title()
                        except PlaywrightError:
                            title = "(title unavailable)"
                        print(f"- {title} | {page.url}")
                    time.sleep(2)
            except CDPConnectionError as exc:
                print(exc, file=sys.stderr)
                return 1
            print("Playwright disconnected; Chrome remains open.")
            return 0

        print(f"Opening legacy persistent Chromium profile: {settings.profile_path}", flush=True)
        print("Press Enter to close this Chromium window.", flush=True)
        try:
            with manager.open() as session:
                BrowserManager.open_page(session.contexts[0])
                input()
        except (EOFError, KeyboardInterrupt):
            pass
        return 0

    with Database(settings.db_path) as db:
        if args.command == "evaluate":
            scouts = db.get_scouts_for_evaluation(
                platform=args.platform, limit=args.limit, force=args.force,
                replace_provider=args.replace_provider,
            )
            classifier = _classifier(settings)
            if args.replace_provider == classifier.provider and scouts:
                raise ValueError("--replace-provider must differ from CLASSIFIER_PROVIDER")
            print(f"Selected Scouts: {len(scouts)}")
            print(f"Classifier/model: {classifier.provider} / {classifier.model_name}")
            run_id = db.start_run(f"evaluate:{args.platform or 'all'}")
            successful = 0
            stopped_reason = None
            batch_size = settings.codex_batch_size if classifier.provider == "codex" else 1
            for offset in range(0, len(scouts), batch_size):
                batch = scouts[offset:offset + batch_size]
                scout_by_id = {str(scout_id): (scout_id, scout) for scout_id, scout in batch}
                try:
                    if len(batch) == 1 or not hasattr(classifier, "classify_many"):
                        evaluations = {
                            str(scout_id): classifier.classify(scout)
                            for scout_id, scout in batch
                        }
                    else:
                        evaluations = classifier.classify_many(
                            [(str(scout_id), scout) for scout_id, scout in batch]
                        )
                    if not isinstance(evaluations, dict) or set(evaluations) - set(scout_by_id):
                        raise ValueError("Classifier returned unexpected Scout IDs")
                    for internal_id, (scout_id, scout) in scout_by_id.items():
                        if internal_id not in evaluations:
                            continue
                        evaluation = _finalize_evaluation(evaluations[internal_id], scout)
                        if args.replace_provider:
                            saved = db.replace_evaluation_if_provider(
                                scout_id, run_id, evaluation,
                                expected_provider=args.replace_provider,
                                provider=classifier.provider, model_name=classifier.model_name,
                            )
                            if not saved:
                                raise RuntimeError("Evaluation provider changed before save; original result preserved")
                        else:
                            db.save_evaluation(
                                scout_id, run_id, evaluation,
                                provider=classifier.provider, model_name=classifier.model_name,
                            )
                        successful += 1
                    if len(evaluations) < len(batch):
                        missing = len(batch) - len(evaluations)
                        stopped_reason = f"incomplete batch: {missing} Scout result(s) missing; original evaluations preserved"
                        break
                except CodexClassifierError as exc:
                    stopped_reason = f"{exc.category}: {exc}"
                    break
                except Exception as exc:
                    stopped_reason = f"classifier error ({type(exc).__name__}); current batch stopped"
                    break
            db.finish_run(run_id, error=stopped_reason)
            _, results = db.get_recent_results(run_id)
            html, json_path = generate_report(
                settings.output_path, results, model_name=classifier.model_name,
            )
            print(f"Evaluated successfully: {successful}")
            print(f"Remaining: {len(scouts) - successful}")
            print(f"Stopped reason: {stopped_reason or 'none'}")
            print(f"HTML report: {html}")
            print(f"JSON report: {json_path}")
            return 1 if stopped_reason else 0

        if args.command == "status":
            print("DB: OK")
            print(f"Browser mode: {settings.browser_mode}")
            print(f"CDP endpoint: {settings.cdp_endpoint}")
            print(f"Browser profile exists: {'yes' if settings.profile_path.exists() else 'no'}")
            cdp_manager = BrowserManager(
                settings.profile_path,
                mode="cdp",
                cdp_endpoint=settings.cdp_endpoint,
            )
            try:
                with cdp_manager.open() as session:
                    print("Chrome reachable: yes")
                    print(f"Chrome contexts: {len(session.contexts)}")
                    print(f"Chrome pages: {len(session.pages)}")
            except CDPConnectionError:
                print("Chrome reachable: no")
                print("Chrome contexts: unavailable")
                print("Chrome pages: unavailable")
            print(f"Classifier provider: {settings.classifier_provider}")
            print(f"Classifier model: {_configured_model_name(settings)}")
            print(f"Codex CLI available: {'yes' if codex_cli_available() else 'no'}")
            print(f"Codex authenticated: {'yes' if codex_auth_mode() == 'chatgpt' else 'no'}")
            print(f"GEMINI_API_KEY configured: {'yes' if settings.gemini_api_key else 'no'}")
            print(f"Current classifier: {settings.classifier_provider.capitalize()}")
            print(f"Processed scouts: {db.count_processed()}")
            return 0

        if args.command == "report":
            run, results = db.get_recent_results()
            if run is None:
                print("No completed scan run yet.")
                return 1
            html, json_path = generate_report(settings.output_path, results)
            print(f"HTML report: {html}")
            print(f"JSON report: {json_path}")
            return 0

        adapter = ADAPTERS[args.platform]()
        if args.platform not in {"generic", "green", "type"}:
            print("Adapter not implemented yet.")
            return 0
        if args.platform == "type":
            if settings.browser_mode != "cdp":
                print("type scanning requires BROWSER_MODE=cdp.", file=sys.stderr)
                return 2
            try:
                manager = BrowserManager(
                    settings.profile_path, mode="cdp", cdp_endpoint=settings.cdp_endpoint,
                )
                with manager.open() as session:
                    if not session.contexts:
                        raise RuntimeError("Chrome has no browser context")
                    page = session.contexts[0].new_page()
                    try:
                        page.goto(adapter.scout_list_url, wait_until="domcontentloaded", timeout=15000)
                        if not adapter.is_logged_in(page):
                            raise RuntimeError("type is not logged in; log in manually in Chrome first")
                        refs = adapter.get_scout_list(page, limit=settings.list_scan_limit)
                        print(f"type list entries: {len(refs)} (limit {settings.list_scan_limit})", flush=True)
                        run_id = db.start_run(args.platform)
                        stats = {key: 0 for key in (
                            "list_scanned", "already_seen", "already_seen_jobs", "TITLE_SKIP",
                            "TITLE_REVIEW", "DETAIL", "title_skipped", "detail_fetched",
                            "job_details_fetched", "locally_skipped", "sent_to_classifier",
                            "too_old", "unknown_date",
                        )}
                        consecutive_seen = 0
                        today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
                        cutoff = today - timedelta(days=settings.scout_max_age_days)
                        pending_ids = {
                            ref.external_id for ref in refs
                            if ref.received_on is not None and ref.received_on >= cutoff
                            and db.type_list_status(ref.external_id) == "pending"
                        }
                        candidates = []
                        try:
                            # Phase 1: list-only discovery. No detail page is opened here.
                            for ref in refs:
                                stats["list_scanned"] += 1
                                status = db.type_list_status(ref.external_id)
                                legacy_seen = status is None and db.is_seen(
                                    Scout(id=ref.external_id, platform="type")
                                )
                                seen = status in {"completed", "title_skip", "legacy_seen"} or legacy_seen
                                decisions = prefilter_jobs(ref.job_titles)
                                for index, item in enumerate(decisions):
                                    stats[item.decision] += 1
                                    if item.decision == "TITLE_SKIP":
                                        stats["title_skipped"] += 1
                                    db.save_scan_audit(
                                        scan_run_id=run_id, platform="type",
                                        external_id=_type_list_job_id(ref.external_id, index),
                                        company=ref.company_name,
                                        job_title=ref.job_titles[index] if ref.job_titles else None,
                                        decision=item.decision, reason=item.reason,
                                        already_seen=seen,
                                        received_on=ref.received_on.isoformat() if ref.received_on else None,
                                        received_at=None,
                                    )
                                if ref.received_on is None:
                                    stats["unknown_date"] += 1
                                    consecutive_seen = 0
                                    continue
                                if ref.received_on < cutoff:
                                    stats["too_old"] += 1
                                    consecutive_seen = 0
                                    continue
                                pending_ids.discard(ref.external_id)
                                if seen:
                                    if legacy_seen:
                                        db.save_type_list_item(
                                            external_id=ref.external_id, company_name=ref.company_name,
                                            job_title=ref.job_title or " / ".join(ref.job_titles) or None,
                                            received_on=ref.received_on.isoformat(), received_at=None,
                                            url=ref.url, status="legacy_seen",
                                        )
                                    stats["already_seen"] += 1
                                    consecutive_seen += 1
                                    if consecutive_seen >= settings.seen_stop_threshold and not pending_ids:
                                        break
                                    continue
                                consecutive_seen = 0
                                all_skipped = all(item.decision == "TITLE_SKIP" for item in decisions)
                                if status is None:
                                    db.save_type_list_item(
                                        external_id=ref.external_id, company_name=ref.company_name,
                                        job_title=ref.job_title or " / ".join(ref.job_titles) or None,
                                        received_on=ref.received_on.isoformat(),
                                        received_at=None, url=ref.url,
                                        status="title_skip" if all_skipped else "pending",
                                    )
                                elif all_skipped:
                                    db.skip_type_list_item(ref.external_id)
                                if all_skipped:
                                    continue
                                candidates.append((ref, {
                                    index for index, item in enumerate(decisions)
                                    if item.decision != "TITLE_SKIP"
                                }))

                            # Phase 2: only new/pending candidates get detail/JD and classification.
                            print(f"type detail candidates: {len(candidates)}", flush=True)
                            classifier = None
                            for ref, include_indices in candidates:
                                raw_jobs = adapter.get_scout_detail(
                                    page, ref, include_indices=include_indices,
                                )
                                stats["detail_fetched"] += 1
                                print(f"type detail fetched: {stats['detail_fetched']}", flush=True)
                                for raw in raw_jobs:
                                    scout = adapter.normalize_scout(raw)
                                    list_index = raw.get("_list_index")
                                    if list_index is not None and 0 <= list_index < len(ref.job_titles or ("",)):
                                        db.mark_scan_audit_detail(
                                            run_id, _type_list_job_id(ref.external_id, list_index),
                                            scout.id or _type_list_job_id(ref.external_id, list_index),
                                        )
                                    else:
                                        db.save_scan_audit(
                                            scan_run_id=run_id, platform="type",
                                            external_id=scout.id or ref.external_id,
                                            company=scout.company_name, job_title=scout.job_title,
                                            decision="DETAIL",
                                            reason="详情职位无法可靠匹配列表标题，保守读取。",
                                            already_seen=db.is_seen(scout),
                                            received_on=ref.received_on.isoformat() if ref.received_on else None,
                                            received_at=None, detail_fetched=True,
                                        )
                                        stats["DETAIL"] += 1
                                    stats["job_details_fetched"] += 1
                                    if db.is_seen(scout):
                                        stats["already_seen_jobs"] += 1
                                        continue
                                    evaluation = type_post_detail_hard_rule(scout)
                                    if evaluation is None and prefilter_title(scout.job_title).decision == "TITLE_SKIP":
                                        evaluation = Evaluation(
                                            verdict="SKIP", confidence=0.9,
                                            summary="职位标题明确不属于 IT 或 Cloud/Infrastructure 方向。",
                                            reasons=[], concerns=["仅按明确的职位标题做本地排除。"],
                                        )
                                    if evaluation is not None:
                                        scout_id = db.save_scout(scout)
                                        db.save_evaluation(
                                            scout_id, run_id, evaluation,
                                            provider="local", model_name="type-hard-rule",
                                        )
                                        stats["locally_skipped"] += 1
                                        continue
                                    if classifier is None:
                                        classifier = _classifier(settings)
                                    evaluation = _classify(classifier, scout)
                                    scout_id = db.save_scout(scout)
                                    db.save_evaluation(
                                        scout_id, run_id, evaluation,
                                        provider=classifier.provider, model_name=classifier.model_name,
                                    )
                                    stats["sent_to_classifier"] += 1
                                db.finish_type_list_item(ref.external_id)
                            db.finish_run(run_id)
                        except Exception as exc:
                            db.finish_run(run_id, error=type(exc).__name__)
                            raise
                    finally:
                        page.close()
            except CDPConnectionError as exc:
                print(exc, file=sys.stderr)
                return 1
            _, results = db.get_recent_results(run_id)
            html, json_path = generate_report(
                settings.output_path, results, scan_stats=stats,
                model_name=_configured_model_name(settings),
            )
            for key, value in stats.items():
                print(f"{key}: {value}")
            verdicts = {key: sum(evaluation.verdict == key for _, evaluation, _ in results)
                        for key in ("KEEP", "MAYBE", "SKIP")}
            print(" ".join(f"{key}: {value}" for key, value in verdicts.items()))
            print(f"New scouts: {len(results)}")
            print(f"HTML report: {html}")
            print(f"JSON report: {json_path}")
            return 0
        if args.platform == "green":
            if settings.browser_mode != "cdp":
                print("Green scanning requires BROWSER_MODE=cdp.", file=sys.stderr)
                return 2
            try:
                manager = BrowserManager(
                    settings.profile_path,
                    mode=settings.browser_mode,
                    cdp_endpoint=settings.cdp_endpoint,
                )
                with manager.open() as session:
                    if not session.contexts:
                        raise RuntimeError("Chrome has no browser context")
                    page = session.contexts[0].new_page()
                    try:
                        page.goto(adapter.scout_list_url, wait_until="domcontentloaded", timeout=15000)
                        if not adapter.is_logged_in(page):
                            raise RuntimeError("Green is not logged in; log in manually in Chrome first")
                        refs = adapter.get_scout_list(page)
                        print(f"Green Scout candidates: {len(refs)} (limit 30)", flush=True)
                        classifier = _classifier(settings)
                        print(f"Classifier/model: {classifier.provider} / {classifier.model_name}", flush=True)
                        run_id = db.start_run(args.platform)
                        details_read = 0
                        try:
                            for ref in refs:
                                if db.is_seen(Scout(id=ref.external_id, platform="green")):
                                    break
                                scout = adapter.normalize_scout(adapter.get_scout_detail(page, ref))
                                details_read += 1
                                scout_id = db.save_scout(scout)
                                db.save_evaluation(
                                    scout_id, run_id, _classify(classifier, scout),
                                    provider=classifier.provider, model_name=classifier.model_name,
                                )
                                print(f"Green Scout details read: {details_read}", flush=True)
                            db.finish_run(run_id)
                        except Exception as exc:
                            db.finish_run(run_id, error=str(exc))
                            raise
                    finally:
                        page.close()
            except CDPConnectionError as exc:
                print(exc, file=sys.stderr)
                return 1
            _, results = db.get_recent_results(run_id)
            html, json_path = generate_report(settings.output_path, results)
            print(f"New scouts: {len(results)}")
            print(f"HTML report: {html}")
            print(f"JSON report: {json_path}")
            return 0

        classifier = _classifier(settings)
        run_id = db.start_run(args.platform)
        try:
            for raw in adapter.get_scout_list(None):
                scout = adapter.normalize_scout(adapter.get_scout_detail(None, raw))
                if db.is_seen(scout):
                    continue
                scout_id = db.save_scout(scout)
                db.save_evaluation(
                    scout_id, run_id, _classify(classifier, scout),
                    provider=classifier.provider, model_name=classifier.model_name,
                )
            db.finish_run(run_id)
        except Exception as exc:
            db.finish_run(run_id, error=str(exc))
            raise
        _, results = db.get_recent_results(run_id)
        html, json_path = generate_report(settings.output_path, results)
        print(f"New scouts: {len(results)}")
        print(f"HTML report: {html}")
        print(f"JSON report: {json_path}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
