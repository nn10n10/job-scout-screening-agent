"""Fixed daily workflow controller; raw process output never reaches the UI."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading


def daily_runner(db_path: Path, emit) -> int:
    with subprocess.Popen(
        [sys.executable, '-u', '-m', 'scout_agent.web.daily_worker', str(db_path)],
        shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors='replace',
    ) as process:
        for line in process.stdout:
            emit(line.strip())
        return process.wait()


class DailyRunManager:
    def __init__(self, db_path: Path, runner=None):
        self.db_path = db_path
        self.runner = runner or daily_runner
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.state = dict(state='idle', stage=None, started_at=None,
                          finished_at=None, safe_error=None, latest_summary=None)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        # Only recorded times are reliable across restarts. Historical reports
        # are deliberately not read, and no summary is inferred from evaluations.
        if result['state'] == 'idle' and self.db_path.is_file():
            try:
                connection = sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    row = connection.execute(
                        "SELECT started_at,finished_at FROM scan_runs "
                        "WHERE platform='daily' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                finally:
                    connection.close()
                if row:
                    result['started_at'] = datetime.fromisoformat(row[0]).isoformat()
                    result['finished_at'] = datetime.fromisoformat(row[1]).isoformat()
            except (sqlite3.Error, ValueError, TypeError):
                pass
        return result

    def start(self):
        with self.lock:
            if self.state['state'] == 'running':
                return False
            self.state.update(state='running', stage=None,
                              started_at=datetime.now(timezone.utc).isoformat(),
                              finished_at=None, safe_error=None, latest_summary=None)
        try:
            threading.Thread(target=self._run, daemon=True).start()
        except Exception:
            self._finish(False)
        return True

    def _emit(self, line):
        match = re.fullmatch(r'Daily verdicts: KEEP=([0-9]{1,9}) MAYBE=([0-9]{1,9}) SKIP=([0-9]{1,9})', line)
        if match:
            with self.lock:
                self.state['latest_summary'] = dict(zip(('KEEP', 'MAYBE', 'SKIP'), map(int, match.groups())))

    def _finish(self, success):
        with self.lock:
            self.state.update(state='completed' if success else 'failed', stage=None,
                              finished_at=datetime.now(timezone.utc).isoformat(),
                              safe_error=None if success else '筛选未完整完成，请在本机检查运行环境后重试。')

    def _run(self):
        success = False
        try:
            success = self.runner(self.db_path, self._emit) == 0
        except Exception:
            pass
        finally:
            self._finish(success)
