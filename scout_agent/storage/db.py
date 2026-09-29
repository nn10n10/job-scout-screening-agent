from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scout_agent.models.evaluation import Evaluation, EvaluationMetadata
from scout_agent.models.scout import Scout
from scout_agent.local_reprocess import LocalResult


class Database:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        if read_only:
            uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        if read_only:
            return
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scouts (
                id INTEGER PRIMARY KEY,
                platform TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                external_id TEXT,
                url TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(platform, dedupe_key)
            );
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY,
                platform TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY,
                scout_id INTEGER NOT NULL UNIQUE REFERENCES scouts(id),
                run_id INTEGER NOT NULL REFERENCES scan_runs(id),
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'legacy',
                model_name TEXT NOT NULL DEFAULT 'unknown',
                evaluated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS type_list_items (
                external_id TEXT PRIMARY KEY,
                company_name TEXT,
                job_title TEXT,
                received_on TEXT,
                received_at TEXT,
                url TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'title_skip', 'completed', 'legacy_seen')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS doda_list_items (
                external_id TEXT PRIMARY KEY,
                company_name TEXT,
                job_title TEXT,
                age_days INTEGER,
                url TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'title_skip', 'completed', 'too_old')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mynavi_list_items (
                external_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                delivery_id TEXT NOT NULL,
                company_name TEXT,
                scout_title TEXT,
                received_on TEXT,
                url TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'title_skip', 'completed', 'too_old')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_audit (
                id INTEGER PRIMARY KEY,
                scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
                platform TEXT NOT NULL,
                external_id TEXT NOT NULL,
                company TEXT,
                job_title TEXT,
                list_title TEXT,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                detail_decision TEXT,
                detail_reason TEXT,
                detail_fetched INTEGER NOT NULL DEFAULT 0,
                already_seen INTEGER NOT NULL DEFAULT 0,
                received_on TEXT,
                received_at TEXT,
                UNIQUE(scan_run_id, external_id)
            );
            CREATE TABLE IF NOT EXISTS local_reprocess (
                scout_id INTEGER PRIMARY KEY REFERENCES scouts(id),
                platform TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('local_skip', 'classifier_candidate')),
                reason TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                run_id INTEGER NOT NULL REFERENCES scan_runs(id)
            );
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(evaluations)")}
        if "provider" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN provider TEXT NOT NULL DEFAULT 'legacy'")
        if "model_name" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN model_name TEXT NOT NULL DEFAULT 'unknown'")
        if "evaluated_at" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN evaluated_at TEXT")
        audit_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(scan_audit)")}
        if "list_title" not in audit_columns:
            self.conn.execute("ALTER TABLE scan_audit ADD COLUMN list_title TEXT")
        if "detail_decision" not in audit_columns:
            self.conn.execute("ALTER TABLE scan_audit ADD COLUMN detail_decision TEXT")
        if "detail_reason" not in audit_columns:
            self.conn.execute("ALTER TABLE scan_audit ADD COLUMN detail_reason TEXT")
        # Existing doda audits already have stable job IDs; recover the original
        # headline used for the title decision without touching evaluations.
        self.conn.execute(
            "UPDATE scan_audit SET list_title=("
            "SELECT job_title FROM doda_list_items WHERE external_id=scan_audit.external_id"
            ") WHERE platform='doda' AND list_title IS NULL "
            "AND reason NOT LIKE '详情发现独立 job ID%'"
        )
        # Green V0.2 always used MockClassifier. Do not guess the provider of other legacy rows.
        self.conn.execute(
            "UPDATE evaluations SET provider='mock', model_name='mock' "
            "WHERE provider='legacy' AND scout_id IN (SELECT id FROM scouts WHERE platform='green')"
        )
        self.conn.execute("UPDATE evaluations SET evaluated_at=created_at WHERE evaluated_at IS NULL")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def is_seen(self, scout: Scout) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM scouts s JOIN evaluations e ON e.scout_id=s.id "
            "WHERE s.platform=? AND s.dedupe_key=?",
            (scout.platform, scout.dedupe_key),
        ).fetchone() is not None

    def has_stored_scout(self, scout: Scout) -> bool:
        """A captured but unevaluated Scout is pending, not a new detail fetch."""
        return self.conn.execute(
            "SELECT 1 FROM scouts WHERE platform=? AND dedupe_key=?",
            (scout.platform, scout.dedupe_key),
        ).fetchone() is not None

    def type_list_status(self, external_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM type_list_items WHERE external_id=?", (external_id,)
        ).fetchone()
        return str(row["status"]) if row else None

    def doda_list_status(self, external_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM doda_list_items WHERE external_id=?", (external_id,)
        ).fetchone()
        return str(row["status"]) if row else None

    def save_doda_list_item(
        self, *, external_id: str, company_name: str | None, job_title: str | None,
        age_days: int | None, url: str, status: str,
    ) -> None:
        if status not in {"pending", "title_skip", "completed", "too_old"}:
            raise ValueError("Invalid doda list status")
        self.conn.execute(
            "INSERT OR IGNORE INTO doda_list_items "
            "(external_id,company_name,job_title,age_days,url,status,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (external_id, company_name, job_title, age_days, url, status,
             datetime.now().isoformat()),
        )
        self.conn.commit()

    def set_doda_list_status(self, external_id: str, status: str) -> None:
        if status not in {"pending", "title_skip", "completed", "too_old"}:
            raise ValueError("Invalid doda list status")
        self.conn.execute(
            "UPDATE doda_list_items SET status=? WHERE external_id=?",
            (status, external_id),
        )
        self.conn.commit()

    def doda_pending_ids(self) -> set[str]:
        return {
            str(row["external_id"]) for row in self.conn.execute(
                "SELECT external_id FROM doda_list_items WHERE status='pending'"
            )
        }

    def mynavi_list_status(self, external_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM mynavi_list_items WHERE external_id=?", (external_id,)
        ).fetchone()
        return str(row["status"]) if row else None

    def save_mynavi_list_item(
        self, *, external_id: str, job_id: str, delivery_id: str,
        company_name: str | None, scout_title: str | None,
        received_on: str | None, url: str, status: str,
    ) -> None:
        if status not in {"pending", "title_skip", "completed", "too_old"}:
            raise ValueError("Invalid マイナビ list status")
        self.conn.execute(
            "INSERT OR IGNORE INTO mynavi_list_items "
            "(external_id,job_id,delivery_id,company_name,scout_title,received_on,url,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (external_id, job_id, delivery_id, company_name, scout_title,
             received_on, url, status, datetime.now().isoformat()),
        )
        self.conn.commit()

    def set_mynavi_list_status(self, external_id: str, status: str) -> None:
        if status not in {"pending", "title_skip", "completed", "too_old"}:
            raise ValueError("Invalid マイナビ list status")
        self.conn.execute(
            "UPDATE mynavi_list_items SET status=? WHERE external_id=?", (status, external_id)
        )
        self.conn.commit()

    def mynavi_pending_ids(self) -> set[str]:
        return {
            str(row["external_id"]) for row in self.conn.execute(
                "SELECT external_id FROM mynavi_list_items WHERE status='pending'"
            )
        }

    def save_type_list_item(
        self, *, external_id: str, company_name: str | None, job_title: str | None,
        received_on: str | None, received_at: str | None, url: str, status: str,
    ) -> None:
        if status not in {"pending", "title_skip", "completed", "legacy_seen"}:
            raise ValueError("Invalid type list status")
        self.conn.execute(
            "INSERT OR IGNORE INTO type_list_items "
            "(external_id,company_name,job_title,received_on,received_at,url,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (external_id, company_name, job_title, received_on, received_at, url,
             status, datetime.now().isoformat()),
        )
        self.conn.commit()

    def finish_type_list_item(self, external_id: str) -> None:
        self.conn.execute(
            "UPDATE type_list_items SET status='completed' WHERE external_id=? AND status='pending'",
            (external_id,),
        )
        self.conn.commit()

    def skip_type_list_item(self, external_id: str) -> None:
        self.conn.execute(
            "UPDATE type_list_items SET status='title_skip' WHERE external_id=? AND status='pending'",
            (external_id,),
        )
        self.conn.commit()

    def save_scan_audit(
        self, *, scan_run_id: int, platform: str, external_id: str,
        company: str | None, job_title: str | None, decision: str, reason: str,
        already_seen: bool, received_on: str | None, received_at: str | None,
        detail_fetched: bool = False, list_title: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO scan_audit "
            "(scan_run_id,platform,external_id,company,job_title,list_title,decision,reason,"
            "detail_fetched,already_seen,received_on,received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan_run_id, platform, external_id, company, job_title, list_title, decision,
             reason, int(detail_fetched), int(already_seen), received_on, received_at),
        )
        self.conn.commit()

    def mark_scan_audit_detail(
        self, scan_run_id: int, list_external_id: str, job_external_id: str, *,
        company: str | None = None, job_title: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE scan_audit SET detail_fetched=1, external_id=?, "
            "company=COALESCE(?,company), job_title=COALESCE(?,job_title) "
            "WHERE scan_run_id=? AND external_id=?",
            (job_external_id, company, job_title, scan_run_id, list_external_id),
        )
        self.conn.commit()

    def mark_scan_audit_detail_local_skip(
        self, scan_run_id: int, external_id: str, reason: str,
    ) -> None:
        cursor = self.conn.execute(
            "UPDATE scan_audit SET detail_decision='DETAIL_LOCAL_SKIP', detail_reason=? "
            "WHERE scan_run_id=? AND external_id=? AND detail_fetched=1",
            (reason, scan_run_id, external_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("No fetched detail audit row for local skip")
        self.conn.commit()

    def mark_scan_audit_detail_error(
        self, scan_run_id: int, external_id: str, reason: str,
    ) -> None:
        cursor = self.conn.execute(
            "UPDATE scan_audit SET detail_decision='DETAIL_ERROR', detail_reason=? "
            "WHERE scan_run_id=? AND external_id=? AND detail_fetched=0",
            (reason, scan_run_id, external_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("No pending detail audit row for error")
        self.conn.commit()

    def get_scan_audit(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT scan_run_id,platform,external_id,company,job_title,list_title,decision,reason,"
            "detail_decision,detail_reason,"
            "detail_fetched,already_seen,received_on,received_at "
            "FROM scan_audit WHERE scan_run_id=? ORDER BY id", (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_scout(self, scout: Scout) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO scouts(platform,dedupe_key,external_id,url,payload,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (scout.platform, scout.dedupe_key, scout.id, scout.url,
             scout.model_dump_json(), datetime.now().isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM scouts WHERE platform=? AND dedupe_key=?",
            (scout.platform, scout.dedupe_key),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def get_stored_scouts_with_jd(
        self, platform: str,
    ) -> list[tuple[int, Scout, str | None]]:
        """Return captured details only; never request missing JDs from a website."""
        rows = self.conn.execute(
            "SELECT s.id,s.payload,e.provider FROM scouts s "
            "LEFT JOIN evaluations e ON e.scout_id=s.id "
            "WHERE s.platform=? ORDER BY s.id", (platform,),
        ).fetchall()
        stored = []
        for row in rows:
            scout = Scout.model_validate_json(row["payload"])
            if scout.jd_text and scout.jd_text.strip():
                stored.append((int(row["id"]), scout, row["provider"]))
        return stored

    def save_local_reprocess_result(
        self, scout_id: int, platform: str, run_id: int, result: LocalResult,
    ) -> bool:
        """Persist one decision; replace only mock/legacy/local evaluations.

        Returns whether an evaluation was inserted or updated. Paid evaluations
        remain untouched even when the current local rule would skip the Scout.
        """
        timestamp = datetime.now().isoformat()
        changed = False
        with self.conn:
            row = self.conn.execute(
                "SELECT platform FROM scouts WHERE id=?", (scout_id,),
            ).fetchone()
            if row is None or row["platform"] != platform:
                raise ValueError("Stored Scout and platform do not match")
            if result.decision == "local_skip":
                if result.evaluation is None or not result.model_name:
                    raise ValueError("Local skip requires an evaluation and model name")
                cursor = self.conn.execute(
                    "INSERT INTO evaluations"
                    "(scout_id,run_id,payload,created_at,provider,model_name,evaluated_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scout_id) DO UPDATE SET "
                    "run_id=excluded.run_id,payload=excluded.payload,provider=excluded.provider,"
                    "model_name=excluded.model_name,evaluated_at=excluded.evaluated_at "
                    "WHERE evaluations.provider IN ('mock','legacy','local')",
                    (scout_id, run_id, result.evaluation.model_dump_json(), timestamp,
                     "local", result.model_name, timestamp),
                )
                changed = cursor.rowcount == 1
            self.conn.execute(
                "INSERT INTO local_reprocess"
                "(scout_id,platform,decision,reason,processed_at,run_id) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(scout_id) DO UPDATE SET "
                "platform=excluded.platform,decision=excluded.decision,reason=excluded.reason,"
                "processed_at=excluded.processed_at,run_id=excluded.run_id",
                (scout_id, platform, result.decision, result.reason, timestamp, run_id),
            )
        return changed

    def save_evaluation(
        self, scout_id: int, run_id: int, evaluation: Evaluation, *,
        provider: str = "mock", model_name: str = "mock",
        evaluated_at: datetime | None = None,
    ) -> None:
        timestamp = (evaluated_at or datetime.now()).isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT INTO evaluations(scout_id,run_id,payload,created_at,provider,model_name,evaluated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scout_id) DO UPDATE SET "
                "run_id=excluded.run_id, payload=excluded.payload, provider=excluded.provider, "
                "model_name=excluded.model_name, evaluated_at=excluded.evaluated_at",
                (scout_id, run_id, evaluation.model_dump_json(), timestamp,
                 provider, model_name, timestamp),
            )
            self._clear_classifier_candidate(scout_id)

    def save_evaluation_if_absent(
        self, scout_id: int, run_id: int, evaluation: Evaluation, *,
        provider: str, model_name: str,
    ) -> bool:
        """Daily retries may fill a missing evaluation, never replace one."""
        timestamp = datetime.now().isoformat()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO evaluations(scout_id,run_id,payload,created_at,provider,model_name,evaluated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scout_id) DO NOTHING",
                (scout_id, run_id, evaluation.model_dump_json(), timestamp,
                 provider, model_name, timestamp),
            )
            if cursor.rowcount == 1:
                self._clear_classifier_candidate(scout_id)
        return cursor.rowcount == 1

    def replace_evaluation_if_provider(
        self, scout_id: int, run_id: int, evaluation: Evaluation, *,
        expected_provider: str, provider: str, model_name: str,
    ) -> bool:
        """Update only if the row still has the requested provider; never touch Codex rows by accident."""
        timestamp = datetime.now().isoformat()
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE evaluations SET run_id=?, payload=?, provider=?, model_name=?, evaluated_at=? "
                "WHERE scout_id=? AND provider=?",
                (run_id, evaluation.model_dump_json(), provider, model_name, timestamp,
                 scout_id, expected_provider),
            )
            if cursor.rowcount == 1:
                self._clear_classifier_candidate(scout_id)
        return cursor.rowcount == 1

    def _clear_classifier_candidate(self, scout_id: int) -> None:
        """A successful evaluation consumes the pending eligibility marker."""
        self.conn.execute(
            "DELETE FROM local_reprocess WHERE scout_id=? AND decision='classifier_candidate'",
            (scout_id,),
        )

    def get_scouts_for_evaluation(
        self, *, platform: str | None = None, limit: int | None = None,
        force: bool = False, replace_provider: str | None = None,
        eligible_only: bool = False,
    ) -> list[tuple[int, Scout]]:
        if force and replace_provider is not None:
            raise ValueError("--force and --replace-provider cannot be combined")
        if eligible_only and self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_reprocess'"
        ).fetchone() is None:
            return []
        conditions = []
        params: list[object] = []
        if platform is not None:
            conditions.append("s.platform=?")
            params.append(platform)
        if replace_provider is not None:
            conditions.append("e.provider=?")
            params.append(replace_provider)
        elif not force:
            conditions.append("e.id IS NULL")
        if eligible_only:
            conditions.append(
                "EXISTS (SELECT 1 FROM local_reprocess lr WHERE lr.scout_id=s.id "
                "AND lr.decision='classifier_candidate' "
                "AND (e.id IS NULL OR COALESCE(e.evaluated_at,e.created_at)<lr.processed_at))"
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT s.id, s.payload FROM scouts s "
            "LEFT JOIN evaluations e ON e.scout_id=s.id" + where + " ORDER BY s.id"
        )
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [(int(row["id"]), Scout.model_validate_json(row["payload"])) for row in rows]

    def get_new_scouts(self) -> list[Scout]:
        rows = self.conn.execute(
            "SELECT s.payload FROM scouts s LEFT JOIN evaluations e ON e.scout_id=s.id "
            "WHERE e.id IS NULL ORDER BY s.id"
        ).fetchall()
        return [Scout.model_validate_json(row["payload"]) for row in rows]

    def start_run(self, platform: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO scan_runs(platform,started_at,status) VALUES (?,?,?)",
            (platform, datetime.now().isoformat(), "running"),
        )
        self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def finish_run(self, run_id: int, *, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE scan_runs SET finished_at=?, status=?, error=? WHERE id=?",
            (datetime.now().isoformat(), "failed" if error else "completed", error, run_id),
        )
        self.conn.commit()

    def get_recent_results(
        self, run_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, list[tuple[Scout, Evaluation, EvaluationMetadata]]]:
        if run_id is None:
            row = self.conn.execute(
                "SELECT * FROM scan_runs WHERE status='completed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None, []
        rows = self.conn.execute(
            "SELECT s.payload AS scout_payload, e.payload AS evaluation_payload, "
            "e.provider, e.model_name, e.evaluated_at "
            "FROM evaluations e JOIN scouts s ON s.id=e.scout_id "
            "WHERE e.run_id=? ORDER BY CASE json_extract(e.payload,'$.verdict') "
            "WHEN 'KEEP' THEN 0 WHEN 'MAYBE' THEN 1 ELSE 2 END, e.id",
            (row["id"],),
        ).fetchall()
        return dict(row), [
            (Scout.model_validate_json(item["scout_payload"]),
             Evaluation.model_validate_json(item["evaluation_payload"]),
             EvaluationMetadata(
                 provider=item["provider"], model_name=item["model_name"],
                 evaluated_at=datetime.fromisoformat(item["evaluated_at"]),
             ))
            for item in rows
        ]

    def get_results_for_scout_ids(
        self, scout_ids: set[int],
    ) -> list[tuple[Scout, Evaluation, EvaluationMetadata]]:
        if not scout_ids:
            return []
        placeholders = ",".join("?" for _ in scout_ids)
        rows = self.conn.execute(
            "SELECT s.payload AS scout_payload,e.payload AS evaluation_payload,"
            "e.provider,e.model_name,e.evaluated_at "
            "FROM scouts s JOIN evaluations e ON e.scout_id=s.id "
            f"WHERE s.id IN ({placeholders}) ORDER BY s.id", tuple(sorted(scout_ids)),
        ).fetchall()
        return [
            (Scout.model_validate_json(row["scout_payload"]),
             Evaluation.model_validate_json(row["evaluation_payload"]),
             EvaluationMetadata(
                 provider=row["provider"], model_name=row["model_name"],
                 evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
             ))
            for row in rows
        ]

    def count_processed(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0])
