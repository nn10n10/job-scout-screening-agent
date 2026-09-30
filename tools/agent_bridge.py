#!/usr/bin/env python3
"""Owner-controlled GitHub/Codex development loop. No recruitment-site access."""
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
            return Result(command, 124, stderr="Command timed out; output withheld.")
        return Result(command, process.returncode, stdout, stderr)
    except OSError:
        return Result(command, 127, stderr="Command could not be started.")


def checked(result: Result) -> str:
    if result.returncode:
        # gh/git may echo authenticated URLs or request payloads in errors.
        raise BridgeError(f"{result.command[0]} {result.command[1]} failed (exit {result.returncode}); raw output withheld")
    return result.stdout


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
            raise BridgeError("Unexpected comment API response; expected an array")
        yield page
        remaining = remaining[end:].lstrip()


class State:
    """Atomic metadata only: never store GitHub bodies or Codex/test transcripts."""
    def __init__(self, directory: Path, issue: int):
        self.directory = directory
        self.path = directory / f"issue-{issue}.json"
        self.data = {"processed": [], "pr": None, "worktree": None, "status": "waiting",
                     "inflight": None, "notification": None}
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
            raise BridgeError("Another bridge process is active for this repository") from None
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
                                       "--json", "number,url,state,isCrossRepository")))
        prs = [pr for pr in prs if not pr.get("isCrossRepository", False)]
        opened = [pr for pr in prs if pr["state"] == "OPEN"]
        if len(opened) > 1:
            raise BridgeError("Multiple open PRs for task branch; resolve before continuing")
        if opened:
            return opened[0]
        if prs:
            raise BridgeError("Task PR is closed or merged; refusing to create a duplicate")
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
                raise BridgeError("Task branch is checked out outside a bridge temporary worktree")
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
        environment.update(TMPDIR=str(path), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONPATH=str(path), GIT_TERMINAL_PROMPT="0")
        return run(command, path, input=prompt, env=environment, timeout=self.timeout)

    def verify(self, path: Path) -> list[Result]:
        # Force imports from the worktree rather than the main checkout's editable install.
        environment = os.environ.copy()
        for name in list(environment):
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", name, re.I):
                environment.pop(name)
        environment.update(PYTHONPATH=str(path), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(path))
        results = [run([str(self.python), "-m", "pytest"], path, env=environment,
                       timeout=self.timeout), self.git("diff", "--check", cwd=path),
                   self.git("diff", "--cached", "--check", cwd=path)]
        for result in results:
            print(f"Verify: {shlex.join(result.command)} | exit {result.returncode} | summary: {redact(result.summary)}", flush=True)
            # Captured diagnostics stay local, sanitized, bounded, and outside persistent state.
            if result.returncode:
                print(redact((result.stdout + "\n" + result.stderr)[-1500:]), flush=True)
        return results

    def publish(self, path: Path, comment_id: int, evidence: str) -> dict | None:
        branch = checked(self.git("branch", "--show-current", cwd=path)).strip()
        if branch != self.branch:
            raise BridgeError("Worktree branch changed; refusing commit/push")
        changed = checked(self.git("status", "--porcelain", "--untracked-files=all", cwd=path)).strip()
        if changed:
            checked(self.git("add", "--all", cwd=path))
            filenames = checked(self.git("diff", "--cached", "--name-only", "-z", cwd=path)).split("\0")
            for filename in filter(None, filenames):
                item = Path(filename)
                if (any(part in {".codex", ".agents", ".bridge-state", "browser_profiles", "data", "output"}
                        for part in item.parts)
                        or (item.name.startswith(".env") and item.name != ".env.example")
                        or item.name.lower() in {"auth.json", "cookies", "cookies-journal", "local state"}
                        or re.search(r"\.(db|sqlite[3]?)(-|$)|\.(pem|key)$", item.name, re.I)
                        or item.name.startswith("storage_state") or item.suffix == ".har"
                        or not (path / item).resolve().is_relative_to(path.resolve())):
                    raise BridgeError("Sensitive/generated/external file staged; refusing commit/push")
            checked(self.git("diff", "--cached", "--check", cwd=path))
            checked(self.git("commit", "-m", f"Implement issue #{self.issue}, supervisor comment {comment_id}", cwd=path))
        # A previous crash can leave a verified local commit without a push/PR.
        ahead = checked(self.git("rev-list", "--count", f"origin/main..{self.branch}", cwd=path)).strip()
        if not changed and ahead == "0":
            return self.state.data["pr"]
        checked(self.git("push", "--set-upstream", "origin", f"HEAD:refs/heads/{self.branch}", cwd=path))
        pr = self.find_pr()
        if not pr:
            body = (f"Implements the supervisor task tracked in #{self.issue}.\n\n"
                    "Control issue remains open during review. The bridge never merges.\n\n"
                    f"Independent verification:\n{evidence}\n")
            checked(self.gh("pr", "create", "--repo", self.repository, "--base", "main",
                            "--head", self.branch, "--title", f"Implement supervisor task #{self.issue}",
                            "--body-file", "-", input=body))
            pr = self.find_pr()
            if not pr:
                raise BridgeError("PR creation returned without an associated open PR")
        self.state.data["pr"] = pr
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
            self.notify(f"[BRIDGE] Comment {previous} was interrupted. It will not run again; post a new owner instruction to continue.")
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
                self.state.data.update(status="complete", inflight=None)
                self.notify(f"[BRIDGE] Supervisor approved task #{self.issue}; complete. No merge performed.")
                return True
            print(f"Processing {kind} comment {identifier} on {self.branch}", flush=True)
            try:
                path = self.worktree()
                result = self.codex(path, comment["body"])
                print(f"Codex exit {result.returncode}; stdout/stderr captured and withheld", flush=True)
                if result.returncode:
                    raise BridgeError(f"Codex failed (exit {result.returncode}); stdout/stderr captured and withheld")
                results = self.verify(path)
                evidence = "\n".join(f"- `{shlex.join(r.command)}`: exit {r.returncode}; summary: {redact(r.summary)}" for r in results)
                if any(r.returncode for r in results):
                    raise BridgeError(f"Independent verification failed.\n{evidence}")
                pr = self.publish(path, identifier, evidence)
                self.state.data.update(status="review", inflight=None)
                status = f"PR #{pr['number']}: {pr['url']}" if pr else "No changes; no PR created."
                message = f"[BRIDGE] Comment {identifier} verified. {status}\n\n{evidence}"
            except BridgeError as exc:
                self.state.data.update(status="failed", inflight=None)
                message = f"[BRIDGE] Comment {identifier} failed. {exc}\nWaiting for a new owner instruction; no success claimed."
            self.notify(message)
        return False


def positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=positive, required=True, help="GitHub control issue number")
    parser.add_argument("--python", type=Path, help="Test interpreter (default: main checkout/.venv/bin/python)")
    parser.add_argument("--poll-seconds", type=positive, default=30)
    parser.add_argument("--timeout", type=positive, default=1800, help="Codex/pytest timeout in seconds")
    parser.add_argument("--once", action="store_true", help="Process one polling batch, then return")
    parser.add_argument("--inspect", action="store_true", help="Read-only discovery and marker listing; no Codex, writes, or comments")
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
            raise BridgeError("Only repositories owned by nn10n10 are accepted")
        python = (args.python or primary / ".venv/bin/python").absolute()
        state = State(primary / ".bridge-state", args.issue)
        bridge = Bridge(root, repository, args.issue, python, state, timeout=args.timeout)
        if args.inspect:
            state.data["pr"] = bridge.find_pr()
            comments = bridge.comments()
            print(f"Repository: {repository}; issue: {args.issue}; branch: {bridge.branch}")
            print(f"Associated PR: {state.data['pr']['number'] if state.data['pr'] else 'none'}")
            for comment in comments:
                if kind := instruction(comment):
                    print(f"Owner instruction: comment {comment['id']} {kind}")
            return 0
        if not python.is_file():
            raise BridgeError("Test interpreter unavailable; configure --python")
        with task_lock(state.directory):
            # Reload after acquiring lock; another process may have saved state meanwhile.
            bridge.state = State(state.directory, args.issue)
            print(f"Bridge: {repository} issue #{args.issue}; branch {bridge.branch}", flush=True)
            while True:
                try:
                    complete = bridge.poll()
                except BridgeError as exc:
                    print(f"Bridge: {exc}", flush=True)
                    if args.once:
                        return 1
                    complete = False
                if complete or args.once:
                    return 1 if bridge.state.data["status"] == "failed" else 0
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Bridge stopped; interrupted instructions will not rerun on restart.")
        return 130
    except BridgeError as exc:
        print(f"Bridge stopped: {exc}")
        return 1
    except (ValueError, OSError, KeyError):
        print("Bridge stopped: command/state discovery failed; raw output withheld. Check gh/git setup and bridge state.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
