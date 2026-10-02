"""Single background CLI run; only allowlisted telemetry crosses into the UI."""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timezone

from scout_agent.green_discovery import KEYWORDS, SAFE_REASONS
from scout_agent.platform_discovery import PLATFORMS
from scout_agent.forkwell_discovery import SOURCES as FORKWELL_SOURCES
from scout_agent.lapras_search import SOURCES as LAPRAS_SOURCES
from scout_agent.type_search import SOURCES as TYPE_SOURCES
from scout_agent.findy_search import SOURCES as FINDY_SOURCES
SEARCH_SOURCES = {'green': KEYWORDS, 'forkwell': FORKWELL_SOURCES, 'lapras': LAPRAS_SOURCES, 'findy': FINDY_SOURCES, 'type': TYPE_SOURCES}

LIMITS = {'coverage_pages': (2, 1, 15), 'max_depth': (15, 2, 100),
          'max_jobs': (30, 1, 500), 'max_model_jobs': (20, 1, 200),
          'codex_batch_size': (2, 1, 20)}
CATEGORIES = {'authentication', 'quota', 'timeout', 'invalid_json', 'cli_execution_error',
              'browser_unavailable', 'green_safety_stop'}
COUNTS = {'扫描页数', 'NEW 职位', 'KNOWN 职位', '缓存复用', '送入 Codex',
          '已读取详情', '预算待处理', 'TARGET', 'POSSIBLE', 'DROP', '模型调用批次数'}


def validate_config(data):
    if not isinstance(data, dict) or set(data) - {'platform', 'sources', *LIMITS}:
        raise ValueError('搜索参数无效')
    platform = data.get('platform', 'green')
    if not isinstance(platform, str) or platform not in SEARCH_SOURCES:
        raise ValueError('搜索平台无效')
    sources = data.get('sources', list(SEARCH_SOURCES[platform]))
    if not isinstance(sources, list) or not sources or any(
        not isinstance(s, str) or s not in SEARCH_SOURCES[platform] for s in sources
    ) or len(sources) != len(set(sources)):
        raise ValueError('请选择已验证的 source')
    config = {'sources': sources[:]}
    if 'platform' in data:
        config['platform'] = platform
    for name, (default, low, high) in LIMITS.items():
        value = (default if platform in {'lapras', 'type'} and name in {'coverage_pages', 'max_depth'}
                 else data.get(name, default))
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{name} 必须为 {low}–{high} 的整数')
        config[name] = value
    return config


def cli_runner(argv, env, emit):
    # Merge pipes to avoid deadlocks; raw output is never retained or returned.
    with subprocess.Popen(argv, env=env, shell=False, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace') as process:
        while True:
            line = process.stdout.readline(4096)
            if not line:
                break
            if not line.endswith('\n'):
                while line and not line.endswith('\n'):
                    line = process.stdout.readline(4096)
                continue
            emit(line.rstrip('\r\n'))
        return process.wait()


class SearchRunManager:
    def __init__(self, runner=None):
        self.runner = runner or cli_runner
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.state = {'status': 'idle', 'started_at': None, 'finished_at': None,
                      'config': None, 'exit_code': None, 'summary': [], 'error': None,
                      'stats': {}, 'failed_source': None, 'failed_page': None,
                      'safe_reason': None}

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        start = result['started_at']
        end = result['finished_at']
        result['elapsed_seconds'] = int(((datetime.fromisoformat(end) if end else
            datetime.now(timezone.utc)) - datetime.fromisoformat(start)).total_seconds()) if start else 0
        return result

    def start(self, data):
        config = validate_config(data)
        with self.lock:
            if self.state['status'] == 'running':
                return False
            self.state = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
                          'finished_at': None, 'config': config, 'exit_code': None,
                          'summary': [], 'error': None, 'stats': {},
                          'failed_source': None, 'failed_page': None, 'safe_reason': None}
        try:
            threading.Thread(target=self._run, args=(config,), daemon=True).start()
        except Exception:
            self._finish(-1)
        return True

    def _emit(self, line):
        if line.startswith(('Findy safe detail diagnostic: ', 'Type safe source diagnostic: ', 'Type safe detail diagnostic: ')):
            return
        safe = None
        with self.lock:
            try:
                status = json.loads(line)
            except (ValueError, TypeError):
                status = None
            if (isinstance(status, dict) and status.get('status') == 'NEEDS_LOGIN'
                    and status.get('platform') in (*PLATFORMS, 'green')):
                self.state['error'] = 'NEEDS_LOGIN'
                return
            context = re.fullmatch(
                r'(?:Green|Forkwell|LAPRAS|Findy|Type) safety stop: source=(.+) page=([0-9]{1,3}) category=([A-Z_]+)', line)
            if context and line.startswith('Type safety stop: ') and context[1] not in (*TYPE_SOURCES, 'NONE'):
                return
            if context and context[1] in (*KEYWORDS, *FORKWELL_SOURCES, *LAPRAS_SOURCES, *FINDY_SOURCES, *TYPE_SOURCES, 'NONE') and context[3] in SAFE_REASONS:
                page = int(context[2])
                if (context[1] == 'NONE' and page == 0) or (context[1] in (*KEYWORDS, *FORKWELL_SOURCES, *LAPRAS_SOURCES, *FINDY_SOURCES, *TYPE_SOURCES) and page > 0):
                    self.state.update(error='green_safety_stop',
                                      failed_source=None if context[1] == 'NONE' else context[1],
                                      failed_page=page or None, safe_reason=context[3])
                return
            match = re.fullmatch(r'Codex category: ([a-z_]+)', line)
            if match and match[1] in CATEGORIES:
                self.state['error'] = match[1]
            elif line == 'Green 搜索安全停止：请检查 CDP、来源页面与 h1 / 仕事内容；DOM 变化需更新解析器。':
                self.state['error'] = 'green_safety_stop'
            else:
                match = re.fullmatch(r'([^:]+): ([0-9]{1,9})', line)
                if match and match[1] in COUNTS:
                    self.state['stats'][match[1]] = int(match[2])
                    safe = line
                for source in (*KEYWORDS, *FORKWELL_SOURCES, *LAPRAS_SOURCES, *FINDY_SOURCES, *TYPE_SOURCES):
                    if re.fullmatch(r'Search progress: ' + re.escape(source) + r' page [0-9]{1,3}', line):
                        safe = line
                    pages = re.fullmatch(re.escape(source) + r' pages: ([0-9]{1,3}(?:,[0-9]{1,3}){0,100})', line)
                    cursor = re.fullmatch(re.escape(source) + r' cursor: ([0-9]{1,3}) → ([0-9]{1,3})', line)
                    if pages:
                        self.state['stats'].setdefault('source_pages', {})[source] = [int(p) for p in pages[1].split(',')]
                    if cursor:
                        self.state['stats'].setdefault('cursors', {})[source] = {'before': int(cursor[1]), 'after': int(cursor[2])}
                    if pages or cursor:
                        safe = line
                if safe:
                    self.state['summary'] = (self.state['summary'] + [safe])[-40:]

    def _finish(self, code):
        with self.lock:
            self.state.update(status='succeeded' if code == 0 else 'failed', exit_code=code,
                              finished_at=datetime.now(timezone.utc).isoformat())
            if code != 0 and not self.state['error']:
                self.state['error'] = 'cli_execution_error'

    def _run(self, config):
        code = -1
        try:
            argv = [sys.executable, '-u', '-m', 'scout_agent', 'search', config.get('platform', 'green')]
            for source in config['sources']:
                argv.extend(['--keyword', source])
            for name in LIMITS:
                if name != 'codex_batch_size':
                    argv.extend(['--' + name.replace('_', '-'), str(config[name])])
            env = os.environ.copy()
            env['CODEX_BATCH_SIZE'] = str(config['codex_batch_size'])
            code = self.runner(argv, env, self._emit)
        except subprocess.TimeoutExpired:
            with self.lock:
                self.state['error'] = 'timeout'
        except Exception:
            pass
        finally:
            self._finish(code)
