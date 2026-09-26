from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scout_agent.models.evaluation import Evaluation, EvaluationMetadata
from scout_agent.models.scout import Scout


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
            CREATE TABLE IF NOT EXISTS scan_audit (
                id INTEGER PRIMARY KEY,
                scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
                platform TEXT NOT NULL,
                external_id TEXT NOT NULL,
                company TEXT,
                job_title TEXT,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                detail_fetched INTEGER NOT NULL DEFAULT 0,
                already_seen INTEGER NOT NULL DEFAULT 0,
                received_on TEXT,
                received_at TEXT,
                UNIQUE(scan_run_id, external_id)
            );
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(evaluations)")}
        if "provider" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN provider TEXT NOT NULL DEFAULT 'legacy'")
        if "model_name" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN model_name TEXT NOT NULL DEFAULT 'unknown'")
        if "evaluated_at" not in columns:
            self.conn.execute("ALTER TABLE evaluations ADD COLUMN evaluated_at TEXT")
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

    def type_list_status(self, external_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM type_list_items WHERE external_id=?", (external_id,)
        ).fetchone()
        return str(row["status"]) if row else None

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
        detail_fetched: bool = False,
    ) -> None:
        self.conn.execute(
            "INSERT INTO scan_audit "
            "(scan_run_id,platform,external_id,company,job_title,decision,reason,"
            "detail_fetched,already_seen,received_on,received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (scan_run_id, platform, external_id, company, job_title, decision,
             reason, int(detail_fetched), int(already_seen), received_on, received_at),
        )
        self.conn.commit()

    def mark_scan_audit_detail(self, scan_run_id: int, list_external_id: str, job_external_id: str) -> None:
        self.conn.execute(
            "UPDATE scan_audit SET detail_fetched=1, external_id=? "
            "WHERE scan_run_id=? AND external_id=?",
            (job_external_id, scan_run_id, list_external_id),
        )
        self.conn.commit()

    def get_scan_audit(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT scan_run_id,platform,external_id,company,job_title,decision,reason,"
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

    def save_evaluation(
        self, scout_id: int, run_id: int, evaluation: Evaluation, *,
        provider: str = "mock", model_name: str = "mock",
        evaluated_at: datetime | None = None,
    ) -> None:
        timestamp = (evaluated_at or datetime.now()).isoformat()
        self.conn.execute(
            "INSERT INTO evaluations(scout_id,run_id,payload,created_at,provider,model_name,evaluated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scout_id) DO UPDATE SET "
            "run_id=excluded.run_id, payload=excluded.payload, provider=excluded.provider, "
            "model_name=excluded.model_name, evaluated_at=excluded.evaluated_at",
            (scout_id, run_id, evaluation.model_dump_json(), timestamp,
             provider, model_name, timestamp),
        )
        self.conn.commit()

    def replace_evaluation_if_provider(
        self, scout_id: int, run_id: int, evaluation: Evaluation, *,
        expected_provider: str, provider: str, model_name: str,
    ) -> bool:
        """Update only if the row still has the requested provider; never touch Codex rows by accident."""
        timestamp = datetime.now().isoformat()
        cursor = self.conn.execute(
            "UPDATE evaluations SET run_id=?, payload=?, provider=?, model_name=?, evaluated_at=? "
            "WHERE scout_id=? AND provider=?",
            (run_id, evaluation.model_dump_json(), provider, model_name, timestamp,
             scout_id, expected_provider),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def get_scouts_for_evaluation(
        self, *, platform: str | None = None, limit: int | None = None,
        force: bool = False, replace_provider: str | None = None,
    ) -> list[tuple[int, Scout]]:
        if force and replace_provider is not None:
            raise ValueError("--force and --replace-provider cannot be combined")
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

    def count_processed(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0])
