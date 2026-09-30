#!/usr/bin/env python3
"""由仓库 owner 控制的 GitHub/Codex 本地开发桥；不访问招聘网站。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import tempfile
import time

OWNER = "nn10n10"
MARKER = re.compile(r"\A\s*\[SUPERVISOR\]\[(TASK|REVIEW|APPROVED)\](?:\s|$)")


class BridgeError(RuntimeError):
    pass


@dataclass
class Result:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def summary(self) -> str:
        lines = (self.stdout + "\n" + self.stderr).splitlines()
        return next((line.strip("= ") for line in reversed(lines)
                     if re.search(r"\b\d+ (passed|failed|skipped|xfailed|xpassed|warnings?|errors?)\b", line)), "none")


def run(command: list[str], cwd: Path, *, input: str | None = None,
        env: dict[str, str] | None = None, timeout: int = 120) -> Result:
    """All external text goes through stdin/argv, never a shell."""
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, shell=False, env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Child exited between interruption and cleanup.
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return Result(command, 124, stderr="命令超时；原始输出已隐藏。")
        return Result(command, process.returncode, stdout, stderr)
    except OSError:
        return Result(command, 127, stderr="无法启动命令。")


def checked(result: Result) -> str:
    if result.returncode:
        local_diagnostic(result)
        # Only fixed diagnostic parameters travel to GitHub, never raw output/argv.
        raise BridgeError(f"{command_label(result.command)} 失败（退出码 {result.returncode}）；诊断仅输出到本机")
    return result.stdout


def command_label(command: list[str]) -> str:
    label = " ".join(command[:2])
    if command[:2] == ["git", "diff"]:
        label += "".join(f" {arg}" for arg in command[2:] if arg in {
            "--cached", "--check", "--name-only", "-z", "--diff-filter=A"})
    return label


def local_diagnostic(result: Result) -> None:
    # Redact before truncation so truncation cannot split a secret before matching.
    output = redact(result.stderr + "\n" + result.stdout)
    output = "".join(char if char.isprintable() or char in "\n\t" else "?" for char in output)
    print(f"本地诊断：{command_label(result.command)} | 退出码 {result.returncode}\n{output[:1500]}", flush=True)


def runtime_artifact(filename: str) -> bool:
    return any(part in {".bridge-state", ".bridge-tmp", ".pytest_cache", "__pycache__"}
               or part.startswith(("pytest-of-", "codex-bwrap-synthetic-mount-targets-"))
               for part in Path(filename).parts)


def redact(text: str) -> str:
    for name, value in os.environ.items():
        if value and re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", name, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)([\w.-]*(?:token|secret|password|api[_-]?key)[\w.-]*\s*[:=]\s*)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text


def instruction(comment: dict) -> str | None:
    if comment.get("user", {}).get("login") != OWNER:
        return None
    match = MARKER.match(comment.get("body") or "")
    return match.group(1) if match else None


def comment_pages(output: str):
    """gh --paginate emits consecutive JSON arrays, including on older gh releases."""
    decoder = json.JSONDecoder()
    remaining = output.lstrip()
    while remaining:
        page, end = decoder.raw_decode(remaining)
        if not isinstance(page, list):
            raise BridgeError("评论 API 响应异常；应为数组")
        yield page
        remaining = remaining[end:].lstrip()


class State:
    """Atomic metadata only: never store GitHub bodies or Codex/test transcripts."""
    def __init__(self, directory: Path, issue: int):
        self.directory = directory
        self.path = directory / f"issue-{issue}.json"
        self.data = {"processed": [], "pr": None, "worktree": None, "status": "waiting",
                     "inflight": None, "notification": None, "verified_published_sha": None}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


@contextmanager
def task_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "bridge.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError("此仓库已有 bridge 进程运行") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Bridge:
    def __init__(self, root: Path, repository: str, issue: int, python: Path,
                 state: State, *, timeout: int = 1800):
        self.root, self.repository, self.issue = root, repository, issue
        self.python, self.state, self.timeout = python, state, timeout
        self.branch = f"agent/issue-{issue}"

    def git(self, *args: str, cwd: Path | None = None) -> Result:
        return run(["git", *args], cwd or self.root)

    def gh(self, *args: str, input: str | None = None) -> Result:
        return run(["gh", *args], self.root, input=input)

    def find_pr(self) -> dict | None:
        prs = json.loads(checked(self.gh("pr", "list", "--repo", self.repository,
                                       "--head", self.branch, "--base", "main", "--state", "all",
                                       "--json", "number,url,state,isCrossRepository,headRefOid")))
        prs = [pr for pr in prs if not pr.get("isCrossRepository", False)]
        opened = [pr for pr in prs if pr["state"] == "OPEN"]
        if len(opened) > 1:
            raise BridgeError("任务分支存在多个 open PR；请先解决冲突")
        if opened:
            return opened[0]
        if prs:
            raise BridgeError("任务 PR 已关闭或已合并；拒绝创建重复 PR")
        return None

    def comments(self) -> list[dict]:
        sources = [self.issue]
        if self.state.data["pr"]:
            sources.append(self.state.data["pr"]["number"])
        comments = []
        for number in sources:
            pages = comment_pages(checked(self.gh("api", "--paginate",
                                  f"repos/{self.repository}/issues/{number}/comments?per_page=100")))
            for page in pages:
                comments.extend(dict(comment, source=number) for comment in page)
        return sorted(comments, key=lambda comment: comment["id"])

    def worktree(self) -> Path:
        checked(self.git("fetch", "origin"))
        # Only registered worktrees with our private temporary-directory layout are reused.
        registered = checked(self.git("worktree", "list", "--porcelain", "-z"))
        for record in registered.split("\0\0"):
            fields = dict(item.split(" ", 1) for item in record.split("\0") if " " in item)
            if fields.get("branch") != f"refs/heads/{self.branch}":
                continue
            path = Path(fields["worktree"]).resolve()
            if (path == self.root or path.name != "repo"
                    or not path.parent.name.startswith(f"scout-bridge-issue-{self.issue}-")
                    or path.parent.parent != Path(tempfile.gettempdir()).resolve()):
                raise BridgeError("任务分支位于 bridge 临时 worktree 之外")
            checked(self.git("rev-parse", "--show-toplevel", cwd=path))
            self.state.data["worktree"] = str(path)
            self.state.save()
            return path
        path = Path(tempfile.mkdtemp(prefix=f"scout-bridge-issue-{self.issue}-")) / "repo"
        if self.git("show-ref", "--verify", "--quiet", f"refs/heads/{self.branch}").returncode == 0:
            command = ["worktree", "add", str(path), self.branch]
        elif self.git("show-ref", "--verify", "--quiet", f"refs/remotes/origin/{self.branch}").returncode == 0:
            command = ["worktree", "add", "-b", self.branch, str(path), f"origin/{self.branch}"]
        else:
            command = ["worktree", "add", "-b", self.branch, str(path), "origin/main"]
        checked(self.git(*command))
        self.state.data["worktree"] = str(path)
        self.state.save()
        return path

    def codex(self, path: Path, body: str) -> Result:
        issue_body = json.loads(checked(self.gh("issue", "view", str(self.issue),
                                               "--repo", self.repository, "--json", "body")))["body"]
        prompt = (
            f"Development task for {self.repository} issue #{self.issue}.\n"
            f"Current repository/worktree: {path}\n"
            "Read AGENTS.md first and preserve all Scout safety constraints.\n"
            "Modify files ONLY inside this temporary worktree. Do not touch files outside it, "
            "the user's main checkout, .env, credentials, ~/.codex, browser profiles, real data, "
            "or reports. No browsers, recruitment sites, network, or real paid model calls.\n"
            "Run appropriate verification yourself before finishing, using mocked fictional data.\n"
            f"Python interpreter for tests (read/execute only): {self.python}\n"
            "The bridge independently verifies and handles git commit/push/PR; do not commit, "
            "push, merge, change branch or git metadata, or post GitHub messages yourself.\n\n"
            f"Issue specification:\n{issue_body}\n\nFull supervisor instruction:\n{body}"
        )
        command = ["codex", "exec", "--sandbox", "workspace-write", "--ephemeral",
                   "--ignore-user-config", "--ignore-rules", "--color", "never",
                   "--config", 'approval_policy="never"',
                   "--config", "allow_login_shell=false",
                   "--config", 'web_search="disabled"',
                   "--config", "sandbox_workspace_write.writable_roots=[]",
                   "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
                   "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                   "--config", "sandbox_workspace_write.network_access=false",
                   "--config", "apps._default.enabled=false",
                   "--config", "agents.enabled=false", "-C", str(path), "-"]
        environment = os.environ.copy()
        for name in list(environment):
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", name, re.I):
                environment.pop(name)
        environment.update(TMPDIR=str(self.runtime_directory(path)), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONPATH=str(path), GIT_TERMINAL_PROMPT="0")
        return run(command, path, input=prompt, env=environment, timeout=self.timeout)

    def runtime_directory(self, path: Path) -> Path:
        directory = path / ".bridge-state" / "runtime"
        if not directory.resolve().is_relative_to(path.resolve()):
            raise BridgeError("临时目录位于 worktree 之外")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def unstage_runtime_artifacts(self, path: Path) -> None:
        # Recover only newly staged runtime artifacts from older bridge versions.
        # Preserve their files and all source edits; never alter previously tracked files.
        added = checked(self.git("diff", "--cached", "--name-only", "--diff-filter=A", "-z", cwd=path))
        for filename in filter(None, added.split("\0")):
            if runtime_artifact(filename):
                checked(self.git("restore", "--staged", "--", f":(literal){filename}", cwd=path))

    def validate_staging(self, path: Path) -> None:
        filenames = checked(self.git("diff", "--cached", "--name-only", "-z", cwd=path)).split("\0")
        for filename in filter(None, filenames):
            item = Path(filename)
            if (runtime_artifact(filename)
                    or any(part in {".codex", ".agents", ".venv", ".bridge-state", "browser_profiles", "data", "output"}
                    for part in item.parts)
                    or (item.name.startswith(".env") and item.name != ".env.example")
                    or item.name.lower() in {"auth.json", "cookies", "cookies-journal", "local state"}
                    or re.search(r"\.(db|sqlite[3]?)(-|$)|\.(pem|key)$", item.name, re.I)
                    or item.name.startswith("storage_state") or item.suffix == ".har"
                    or not (path / item).resolve().is_relative_to(path.resolve())):
                print(f"本地诊断：拒绝暂存路径 {redact(ascii(filename))}", flush=True)
                raise BridgeError("暂存区包含敏感、生成或外部文件；拒绝 commit/push")

    def verify(self, path: Path) -> list[Result]:
        self.unstage_runtime_artifacts(path)
        self.validate_staging(path)
        # Force imports from the worktree rather than the main checkout's editable install.
        environment = os.environ.copy()
        for name in list(environment):
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", name, re.I):
                environment.pop(name)
        environment.update(PYTHONPATH=str(path), PYTHONDONTWRITEBYTECODE="1",
                           TMPDIR=str(self.runtime_directory(path)))
        results = [run([str(self.python), "-m", "pytest"], path, env=environment,
                       timeout=self.timeout), self.git("diff", "--check", cwd=path),
                   self.git("diff", "--cached", "--check", cwd=path)]
        for result in results:
            print(f"验证： {shlex.join(result.command)} | 退出码 {result.returncode} | 摘要： {redact(result.summary)}", flush=True)
            # Captured diagnostics stay local, sanitized, bounded, and outside persistent state.
            if result.returncode:
                local_diagnostic(result)
        return results

    def publish(self, path: Path, comment_id: int, evidence: str) -> dict | None:
        branch = checked(self.git("branch", "--show-current", cwd=path)).strip()
        if branch != self.branch:
            raise BridgeError("worktree 分支已改变；拒绝 commit/push")
        self.unstage_runtime_artifacts(path)
        self.validate_staging(path)
        changed = checked(self.git("status", "--porcelain", "--untracked-files=all", cwd=path)).strip()
        if changed:
            checked(self.git("add", "--all", cwd=path))
            self.validate_staging(path)
            checked(self.git("diff", "--cached", "--check", cwd=path))
            checked(self.git("commit", "-m", f"实现 issue #{self.issue}，supervisor 评论 {comment_id}", cwd=path))
        # A previous crash can leave a verified local commit without a push/PR.
        ahead = checked(self.git("rev-list", "--count", f"origin/main..{self.branch}", cwd=path)).strip()
        if not changed and ahead == "0":
            return self.state.data["pr"]
        verified_sha = checked(self.git("rev-parse", "HEAD", cwd=path)).strip()
        checked(self.git("push", "--set-upstream", "origin", f"HEAD:refs/heads/{self.branch}", cwd=path))
        pr = self.find_pr()
        if not pr:
            body = (f"实现控制 issue #{self.issue} 中的 supervisor 任务。\n\n"
                    "审核期间控制 issue 保持 open。bridge 绝不自动 merge。\n\n"
                    f"独立验证：\n{evidence}\n")
            checked(self.gh("pr", "create", "--repo", self.repository, "--base", "main",
                            "--head", self.branch, "--title", f"实现 supervisor 任务 #{self.issue}",
                            "--body-file", "-", input=body))
            pr = self.find_pr()
            if not pr:
                raise BridgeError("PR 创建后未找到关联的 open PR")
        self.state.data["pr"] = pr
        self.state.data["verified_published_sha"] = verified_sha
        self.state.save()
        return pr

    def notify(self, message: str) -> None:
        self.state.data["notification"] = message
        self.state.save()
        self.flush_notification()

    def flush_notification(self) -> None:
        message = self.state.data["notification"]
        if message:
            checked(self.gh("issue", "comment", str(self.issue), "--repo", self.repository,
                            "--body-file", "-", input=message))
            self.state.data["notification"] = None
            self.state.save()

    def poll(self) -> bool:
        self.flush_notification()
        if self.state.data["status"] == "complete":
            return True
        if self.state.data["inflight"] is not None:
            previous = self.state.data["inflight"]
            self.state.data.update(inflight=None, status="failed")
            self.notify(f"[BRIDGE] 评论 {previous} 执行中断，不会重复运行；请提交新的 owner 指令以继续。")
        # Refresh even a persisted PR before allowing any model/worktree writes.
        self.state.data["pr"] = self.find_pr()
        self.state.save()
        for comment in self.comments():
            kind = instruction(comment)
            identifier = comment["id"]
            if not kind or identifier in self.state.data["processed"]:
                continue
            if kind == "TASK" and comment["source"] != self.issue:
                continue
            if kind in {"REVIEW", "APPROVED"} and not self.state.data["pr"]:
                continue
            self.state.data["processed"].append(identifier)
            self.state.data["inflight"] = identifier
            self.state.save()  # Claim before starting any model/subprocess work.
            if kind == "APPROVED":
                # Re-read at approval time, not only at the start of the polling batch.
                try:
                    pr = self.find_pr()
                    expected = self.state.data["verified_published_sha"]
                    if not pr or not expected or pr.get("headRefOid") != expected:
                        raise BridgeError("当前 open PR head SHA 与最后独立验证并 push 的 SHA 不一致或记录缺失；请提交新的 [SUPERVISOR][REVIEW] 重新验证")
                except BridgeError as exc:
                    self.state.data.update(status="failed", inflight=None)
                    self.notify(f"[BRIDGE] 拒绝完成任务：{exc}。不会 merge。")
                    continue
                self.state.data.update(status="complete", inflight=None, pr=pr)
                self.notify(f"[BRIDGE] 任务 #{self.issue} 已获批准，commit {expected} 验收完成。未执行 merge。")
                return True
            print(f"正在 {self.branch} 处理 {kind} 评论 {identifier}", flush=True)
            try:
                path = self.worktree()
                result = self.codex(path, comment["body"])
                print(f"Codex 退出码 {result.returncode}；stdout/stderr 已捕获并隐藏", flush=True)
                if result.returncode:
                    raise BridgeError(f"Codex 失败（退出码 {result.returncode}）；stdout/stderr 已捕获并隐藏")
                results = self.verify(path)
                evidence = "\n".join(f"- `{shlex.join(r.command)}`: 退出码 {r.returncode}；摘要： {redact(r.summary)}" for r in results)
                if any(r.returncode for r in results):
                    raise BridgeError(f"独立验证失败。\n{evidence}")
                pr = self.publish(path, identifier, evidence)
                self.state.data.update(status="review", inflight=None)
                status = f"PR #{pr['number']}: {pr['url']}" if pr else "无变更；未创建 PR。"
                message = f"[BRIDGE] 评论 {identifier} 已通过验证。 {status}\n\n{evidence}"
            except BridgeError as exc:
                self.state.data.update(status="failed", inflight=None)
                message = f"[BRIDGE] 评论 {identifier} 失败。 {exc}\n等待新的 owner 指令；未报告成功。"
            self.notify(message)
        return False


def positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须大于零")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("--issue", type=positive, required=True, help="GitHub 控制 issue 编号")
    parser.add_argument("--python", type=Path, help="测试解释器（默认：主 checkout/.venv/bin/python）")
    parser.add_argument("--poll-seconds", type=positive, default=30, help="轮询间隔秒数（默认：30）")
    parser.add_argument("--timeout", type=positive, default=1800, help="Codex/pytest 超时秒数")
    parser.add_argument("--once", action="store_true", help="处理一次轮询后退出")
    parser.add_argument("--inspect", action="store_true", help="只读查看仓库与 marker；不调用 Codex、不写入、不发评论")
    args = parser.parse_args(argv)
    try:
        root = Path(checked(run(["git", "rev-parse", "--show-toplevel"], Path.cwd())).strip()).resolve()
        # Shared location and lock across all checkouts of the same repository.
        common = Path(checked(run(["git", "rev-parse", "--git-common-dir"], root)).strip())
        common = (root / common).resolve()
        primary = common.parent
        remote = checked(run(["git", "remote", "get-url", "origin"], root)).strip()
        repository = json.loads(checked(run(["gh", "repo", "view", remote, "--json", "nameWithOwner"], root)))["nameWithOwner"]
        if not re.fullmatch(r"nn10n10/[A-Za-z0-9_.-]+", repository):
            raise BridgeError("仅接受 nn10n10 拥有的仓库")
        python = (args.python or primary / ".venv/bin/python").absolute()
        state = State(primary / ".bridge-state", args.issue)
        bridge = Bridge(root, repository, args.issue, python, state, timeout=args.timeout)
        if args.inspect:
            state.data["pr"] = bridge.find_pr()
            comments = bridge.comments()
            print(f"仓库：{repository}；issue：{args.issue}；分支：{bridge.branch}")
            print(f"关联 PR： {state.data['pr']['number'] if state.data['pr'] else 'none'}")
            for comment in comments:
                if kind := instruction(comment):
                    print(f"owner 指令：评论 {comment['id']} {kind}")
            return 0
        if not python.is_file():
            raise BridgeError("测试解释器不可用；请配置 --python")
        with task_lock(state.directory):
            # Reload after acquiring lock; another process may have saved state meanwhile.
            bridge.state = State(state.directory, args.issue)
            print(f"开发桥： {repository} issue #{args.issue}；分支 {bridge.branch}", flush=True)
            while True:
                try:
                    complete = bridge.poll()
                except BridgeError as exc:
                    print(f"开发桥： {exc}", flush=True)
                    if args.once:
                        return 1
                    complete = False
                if complete or args.once:
                    return 1 if bridge.state.data["status"] == "failed" else 0
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("开发桥已停止；中断的指令在重启后不会重复运行。")
        return 130
    except BridgeError as exc:
        print(f"开发桥已停止： {exc}")
        return 1
    except (ValueError, OSError, KeyError):
        print("开发桥已停止： 命令或状态读取失败；原始输出已隐藏。请检查 gh/git 配置及 bridge 状态。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
