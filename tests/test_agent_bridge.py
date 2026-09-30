"""Fictional GitHub/git/Codex transport; these tests never invoke real tools or models."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools import agent_bridge as ab


def comment(identifier=10, kind="TASK", *, owner=ab.OWNER, source=1, body=None):
    return {"id": identifier, "user": {"login": owner}, "source": source,
            "body": body if body is not None else f"[SUPERVISOR][{kind}]\nImplement fictional task."}


class Transport:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.comments = {1: [comment()]}
        self.pages = {}
        self.prs = []
        self.registered = None
        self.local_branch = False
        self.remote_branch = False
        self.changed = False
        self.ahead = 0
        self.codex_exit = 0
        self.pytest_exit = 0
        self.diff_exit = 0
        self.notification_exit = 0
        self.push_exit = 0
        self.create_exit = 0
        self.change_branch = False
        self.produce_changes = True
        self.staged_files = ["fictional.py"]

    def __call__(self, command, cwd, **kwargs):
        self.calls.append((command, cwd, kwargs))
        result = ab.Result(command, 0)
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            result.stdout = str(cwd if self.registered else self.root)
        elif command[:3] == ["git", "rev-parse", "--git-common-dir"]:
            result.stdout = str(self.root / ".git")
        elif command[:3] == ["git", "remote", "get-url"]:
            result.stdout = "https://github.com/nn10n10/fictional-repo.git"
        elif command[:3] == ["gh", "repo", "view"]:
            result.stdout = json.dumps({"nameWithOwner": "nn10n10/fictional-repo"})
        elif command[:3] == ["gh", "pr", "list"]:
            result.stdout = json.dumps(self.prs)
        elif command[:2] == ["gh", "api"]:
            number = int(command[-1].split("/issues/")[1].split("/")[0])
            result.stdout = "\n".join(json.dumps(page) for page in self.pages.get(number, [self.comments.get(number, [])]))
        elif command[:3] == ["gh", "issue", "view"]:
            result.stdout = json.dumps({"body": "Fictional complete issue specification."})
        elif command[:3] == ["gh", "issue", "comment"]:
            result.returncode = self.notification_exit
        elif command[:3] == ["gh", "pr", "create"]:
            result.returncode = self.create_exit
            if not result.returncode:
                self.prs = [{"number": 12, "url": "https://github.com/nn10n10/fictional-repo/pull/12", "state": "OPEN"}]
                result.stdout = self.prs[0]["url"]
        elif command[:2] == ["git", "fetch"]:
            pass
        elif command[:3] == ["git", "worktree", "list"]:
            if self.registered:
                result.stdout = f"worktree {self.registered}\0HEAD fictional\0branch refs/heads/agent/issue-1\0\0"
        elif command[:3] == ["git", "worktree", "add"]:
            offset = 5 if "-b" in command else 3
            self.registered = Path(command[offset])
            self.registered.mkdir()
            self.local_branch = True
        elif command[:2] == ["git", "show-ref"]:
            result.returncode = 0 if (self.remote_branch if "remotes" in command[-1] else self.local_branch) else 1
        elif command[:2] == ["codex", "exec"]:
            result.returncode = self.codex_exit
            result.stdout, result.stderr = "captured model output", "captured model diagnostics"
            self.changed = self.produce_changes
        elif command[1:] == ["-m", "pytest"]:
            result.returncode = self.pytest_exit
            result.stdout = "=== 1 failed, 4 passed in 0.03s ===" if result.returncode else "=== 5 passed in 0.03s ==="
        elif command[:2] == ["git", "diff"]:
            if "--name-only" in command:
                result.stdout = "\0".join(self.staged_files) + "\0"
            else:
                result.returncode = self.diff_exit
                result.stderr = "fictional whitespace failure" if result.returncode else ""
        elif command[:2] == ["git", "status"]:
            result.stdout = " M fictional.py" if self.changed else ""
        elif command[:2] == ["git", "branch"]:
            result.stdout = "main" if self.change_branch else "agent/issue-1"
        elif command[:2] == ["git", "add"]:
            pass
        elif command[:2] == ["git", "commit"]:
            self.changed = False
            self.ahead += 1
        elif command[:2] == ["git", "rev-list"]:
            result.stdout = str(self.ahead)
        elif command[:2] == ["git", "push"]:
            result.returncode = self.push_exit
            self.remote_branch = not result.returncode
        else:
            pytest.fail(f"Unexpected subprocess command: {command}")
        return result

    def commands(self, *prefix):
        return [call for call in self.calls if call[0][:len(prefix)] == list(prefix)]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "main"
    root.mkdir()
    directory = root / ".bridge-state"
    directory.mkdir()
    state = ab.State(directory, 1)
    transport = Transport(root)
    monkeypatch.setattr(ab, "run", transport)
    original_mkdtemp = ab.tempfile.mkdtemp
    monkeypatch.setattr(ab.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ab.tempfile, "mkdtemp", lambda **kwargs: original_mkdtemp(dir=tmp_path, **kwargs))
    bridge = ab.Bridge(root, "nn10n10/fictional-repo", 1, root / ".venv/bin/python", state)
    return bridge, transport


@pytest.mark.parametrize("owner,body,expected", [
    ("stranger", "[SUPERVISOR][TASK]\nchange code", None),
    ("NN10N10", "[SUPERVISOR][TASK]", None),
    (ab.OWNER, "Please change code", None),
    (ab.OWNER, "Quoted: [SUPERVISOR][TASK]", None),
    (ab.OWNER, "[SUPERVISOR][TASK]suffix", None),
    (ab.OWNER, "[SUPERVISOR][UNKNOWN]", None),
    (ab.OWNER, "\n[SUPERVISOR][TASK]\nchange code", "TASK"),
    (ab.OWNER, "[SUPERVISOR][REVIEW]", "REVIEW"),
    (ab.OWNER, "[SUPERVISOR][APPROVED]\n", "APPROVED"),
])
def test_only_owner_marked_instructions(owner, body, expected):
    assert ab.instruction(comment(owner=owner, body=body)) == expected


def test_unauthorized_comments_never_run_codex(setup):
    bridge, transport = setup
    transport.comments[1] = [comment(owner="stranger"), comment(11, body="plain prose")]
    assert bridge.poll() is False
    assert not transport.commands("codex")
    assert not transport.commands("git", "worktree", "add")


def test_successful_pr_creation_and_independent_verification(setup):
    bridge, transport = setup
    bridge.poll()
    assert bridge.state.data["status"] == "review"
    assert bridge.state.data["pr"]["number"] == 12
    worktree_command = transport.commands("git", "worktree", "add")[0][0]
    assert worktree_command[3:5] == ["-b", "agent/issue-1"]
    assert worktree_command[-1] == "origin/main"
    model_command, cwd, kwargs = transport.commands("codex", "exec")[0]
    assert cwd != bridge.root
    assert model_command[model_command.index("--sandbox") + 1] == "workspace-write"
    for config in ['approval_policy="never"', "sandbox_workspace_write.writable_roots=[]",
                   "sandbox_workspace_write.exclude_slash_tmp=true",
                   "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                   "sandbox_workspace_write.network_access=false", "apps._default.enabled=false"]:
        assert config in model_command
    assert "--ignore-user-config" in model_command and "--ignore-rules" in model_command
    assert "--add-dir" not in model_command
    prompt = kwargs["input"]
    assert "Read AGENTS.md" in prompt and str(cwd) in prompt
    assert "ONLY inside" in prompt and "verification yourself" in prompt
    assert "Fictional complete issue specification." in prompt
    assert transport.comments[1][0]["body"] in prompt
    tests = [call for call in transport.calls if call[0][1:] == ["-m", "pytest"]]
    assert tests[0][0] == [str(bridge.python), "-m", "pytest"]
    assert tests[0][1] == cwd and tests[0][2]["env"]["PYTHONPATH"] == str(cwd)
    push = transport.commands("git", "push")[0][0]
    assert push[-1] == "HEAD:refs/heads/agent/issue-1"
    pr_command, _, pr_args = transport.commands("gh", "pr", "create")[0]
    assert pr_command[pr_command.index("--base") + 1] == "main"
    assert "--body-file" in pr_command and "Closes" not in pr_args["input"]
    assert "5 passed" in pr_args["input"]
    notification = transport.commands("gh", "issue", "comment")[0][2]["input"]
    assert "PR #12" in notification and "exit 0" in notification
    model_index = transport.calls.index(transport.commands("codex", "exec")[0])
    pytest_index = transport.calls.index(tests[0])
    commit_index = transport.calls.index(transport.commands("git", "commit")[0])
    assert model_index < pytest_index < commit_index
    assert not transport.commands("git", "merge")
    assert not transport.commands("gh", "pr", "merge")


def test_processed_comment_is_not_repeated_after_restart(setup):
    bridge, transport = setup
    bridge.poll()
    bridge.state = ab.State(bridge.state.directory, 1)
    bridge.poll()
    assert len(transport.commands("codex", "exec")) == 1
    assert bridge.state.data["processed"] == [10]


def test_review_reuses_same_worktree_branch_and_pr(setup):
    bridge, transport = setup
    bridge.poll()
    first = bridge.state.data["worktree"]
    transport.comments[12] = [comment(11, "REVIEW", source=12)]
    bridge.state = ab.State(bridge.state.directory, 1)
    bridge.poll()
    assert bridge.state.data["worktree"] == first
    assert len(transport.commands("codex", "exec")) == 2
    assert len(transport.commands("git", "worktree", "add")) == 1
    assert len(transport.commands("gh", "pr", "create")) == 1
    assert len(transport.commands("git", "push")) == 2


def test_issue_review_is_also_accepted(setup):
    bridge, transport = setup
    bridge.poll()
    transport.comments[1].append(comment(11, "REVIEW"))
    bridge.poll()
    assert len(transport.commands("codex", "exec")) == 2


def test_approved_stops_without_model_or_merge(setup):
    bridge, transport = setup
    bridge.poll()
    transport.comments[12] = [comment(11, "APPROVED", source=12)]
    assert bridge.poll() is True
    assert bridge.state.data["status"] == "complete"
    assert len(transport.commands("codex", "exec")) == 1
    bridge.state = ab.State(bridge.state.directory, 1)
    assert bridge.poll() is True
    assert not any("merge" in call[0] for call in transport.calls)


@pytest.mark.parametrize("kind", ["REVIEW", "APPROVED"])
def test_requires_existing_pr_for_review_or_approval(setup, kind):
    bridge, transport = setup
    transport.comments[1] = [comment(kind=kind)]
    bridge.poll()
    assert not transport.commands("codex")
    assert bridge.state.data["status"] == "waiting"


def test_task_marker_on_pr_does_not_start_another_task(setup):
    bridge, transport = setup
    bridge.poll()
    transport.comments[12] = [comment(11, source=12)]
    bridge.poll()
    assert len(transport.commands("codex", "exec")) == 1


def test_codex_failure_waits_for_new_instruction(setup):
    bridge, transport = setup
    transport.codex_exit = 9
    bridge.poll()
    assert bridge.state.data["status"] == "failed"
    assert not transport.commands(str(bridge.python))
    assert not transport.commands("git", "commit")
    assert not transport.commands("git", "push")
    assert "Codex failed (exit 9)" in transport.commands("gh", "issue", "comment")[0][2]["input"]
    bridge.poll()
    assert len(transport.commands("codex", "exec")) == 1
    transport.codex_exit = 0
    transport.comments[1].append(comment(11))
    bridge.poll()
    assert bridge.state.data["status"] == "review"
    assert len(transport.commands("git", "worktree", "add")) == 1


@pytest.mark.parametrize("failure", ["pytest_exit", "diff_exit"])
def test_verification_failure_never_commits_or_pushes(setup, failure, capsys):
    bridge, transport = setup
    setattr(transport, failure, 1)
    bridge.poll()
    assert bridge.state.data["status"] == "failed"
    assert not transport.commands("git", "commit")
    assert not transport.commands("git", "push")
    assert not transport.commands("gh", "pr", "create")
    assert len(transport.commands("git", "diff")) == 2
    notification = transport.commands("gh", "issue", "comment")[0][2]["input"]
    assert "Independent verification failed" in notification
    output = capsys.readouterr().out
    if failure == "pytest_exit":
        assert "1 failed, 4 passed" in output and "1 failed, 4 passed" in notification
    else:
        assert "fictional whitespace failure" in output


def test_interruption_claim_is_durable_and_never_reruns(setup, monkeypatch):
    bridge, transport = setup
    original = bridge.codex
    monkeypatch.setattr(bridge, "codex", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        bridge.poll()
    recovered = ab.State(bridge.state.directory, 1)
    assert recovered.data["inflight"] == 10 and recovered.data["processed"] == [10]
    bridge.state = recovered
    monkeypatch.setattr(bridge, "codex", original)
    bridge.poll()
    assert not transport.commands("codex")
    assert bridge.state.data["status"] == "failed"
    assert "interrupted" in transport.commands("gh", "issue", "comment")[0][2]["input"]


def test_notification_retry_does_not_repeat_model(setup):
    bridge, transport = setup
    transport.notification_exit = 1
    with pytest.raises(ab.BridgeError):
        bridge.poll()
    assert bridge.state.data["notification"]
    transport.notification_exit = 0
    bridge.state = ab.State(bridge.state.directory, 1)
    bridge.poll()
    assert len(transport.commands("codex", "exec")) == 1
    assert bridge.state.data["notification"] is None


@pytest.mark.parametrize("existing", ["local", "remote"])
def test_existing_branch_and_pr_are_reused(setup, existing):
    bridge, transport = setup
    setattr(transport, f"{existing}_branch", True)
    transport.prs = [{"number": 12, "url": "https://github.com/nn10n10/fictional-repo/pull/12", "state": "OPEN"}]
    bridge.poll()
    command = transport.commands("git", "worktree", "add")[0][0]
    assert command[-1] == ("agent/issue-1" if existing == "local" else "origin/agent/issue-1")
    assert ("-b" in command) == (existing == "remote")
    assert not transport.commands("gh", "pr", "create")


@pytest.mark.parametrize("location", ["main", "other"])
def test_refuses_branch_checked_out_outside_bridge_temp_directory(setup, tmp_path, location):
    bridge, transport = setup
    transport.registered = bridge.root if location == "main" else tmp_path / "user-worktree"
    bridge.poll()
    assert bridge.state.data["status"] == "failed"
    assert not transport.commands("codex")
    assert not transport.commands("git", "push")


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_closed_pr_prevents_duplicate_creation(setup, state):
    bridge, transport = setup
    transport.prs = [{"number": 12, "url": "fictional", "state": state}]
    with pytest.raises(ab.BridgeError, match="closed or merged"):
        bridge.poll()
    assert not transport.commands("codex")
    assert not transport.commands("gh", "pr", "create")


def test_comment_shell_payload_is_prompt_data_only(setup):
    bridge, transport = setup
    payload = "[SUPERVISOR][TASK]\n$(touch /outside); `curl evil.invalid`\nrm -rf /"
    transport.comments[1] = [comment(body=payload)]
    bridge.poll()
    assert payload in transport.commands("codex", "exec")[0][2]["input"]
    assert not any(payload in argument for call in transport.calls for argument in call[0])
    assert not any(call[0][0] in {"sh", "bash", "curl", "rm", "touch"} for call in transport.calls)


def test_no_changes_does_not_create_commit_or_pr(setup):
    bridge, transport = setup
    transport.produce_changes = False
    bridge.poll()
    assert bridge.state.data["status"] == "review"
    assert not transport.commands("git", "commit")
    assert not transport.commands("git", "push")
    assert not transport.commands("gh", "pr", "create")
    assert "No changes" in transport.commands("gh", "issue", "comment")[0][2]["input"]


@pytest.mark.parametrize("failure", ["push_exit", "create_exit", "change_branch"])
def test_publish_failure_does_not_report_success(setup, failure):
    bridge, transport = setup
    setattr(transport, failure, 1)
    bridge.poll()
    assert bridge.state.data["status"] == "failed"
    assert "failed" in transport.commands("gh", "issue", "comment")[-1][2]["input"]
    if failure == "change_branch":
        assert not transport.commands("git", "push")


def test_prior_unpublished_commit_can_be_recovered(setup):
    bridge, transport = setup
    transport.produce_changes = False
    transport.local_branch, transport.ahead = True, 1
    bridge.poll()
    assert transport.commands("git", "push")
    assert transport.commands("gh", "pr", "create")
    assert not transport.commands("git", "commit")


def test_state_is_metadata_only_and_model_output_is_hidden(setup, capsys):
    bridge, _ = setup
    bridge.poll()
    saved = bridge.state.path.read_text()
    output = capsys.readouterr().out
    for sensitive in ["captured model output", "captured model diagnostics", "Implement fictional task", "complete issue specification"]:
        assert sensitive not in saved and sensitive not in output


def test_codex_environment_removes_credentials(setup, monkeypatch):
    bridge, transport = setup
    monkeypatch.setenv("GEMINI_API_KEY", "fictional-key")
    monkeypatch.setenv("GH_TOKEN", "fictional-token")
    bridge.poll()
    environment = transport.commands("codex")[0][2]["env"]
    assert "GEMINI_API_KEY" not in environment and "GH_TOKEN" not in environment
    assert environment["TMPDIR"] == bridge.state.data["worktree"]
    verification_environment = transport.commands(str(bridge.python))[0][2]["env"]
    assert "GEMINI_API_KEY" not in verification_environment and "GH_TOKEN" not in verification_environment


def test_diagnostics_redact_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fictional-value")
    text = ab.redact("fictional-value ghp_fictional sk-fictional Authorization: Bearer abc\npassword=xyz https://user:pass@example.invalid")
    for sensitive in ["fictional-value", "ghp_fictional", "sk-fictional", "abc", "xyz", "user:pass"]:
        assert sensitive not in text


def test_summary_includes_warning_skip_and_failure_counts():
    result = ab.Result(["python", "-m", "pytest"], 1,
                       "log\n=== 1 failed, 2 passed, 3 skipped, 1 xfailed, 2 warnings in 0.3s ===\n")
    assert result.summary == "1 failed, 2 passed, 3 skipped, 1 xfailed, 2 warnings in 0.3s"


def test_lock_prevents_concurrent_tasks_and_releases_after_interrupt(tmp_path):
    directory = tmp_path / ".bridge-state"
    with pytest.raises(KeyboardInterrupt):
        with ab.task_lock(directory):
            with pytest.raises(ab.BridgeError, match="Another bridge"):
                with ab.task_lock(directory):
                    pytest.fail("Lock must reject concurrent bridge")
            raise KeyboardInterrupt()
    with ab.task_lock(directory):
        pass


def test_cli_help_does_not_invoke_any_tools(monkeypatch, capsys):
    monkeypatch.setattr(ab, "run", lambda *_a, **_k: pytest.fail("Help must not run tools"))
    with pytest.raises(SystemExit) as exit_info:
        ab.main(["--help"])
    assert exit_info.value.code == 0
    assert "--inspect" in capsys.readouterr().out


def test_cli_inspect_is_read_only(setup, capsys):
    bridge, transport = setup
    assert ab.main(["--issue", "1", "--inspect"]) == 0
    assert "Owner instruction: comment 10 TASK" in capsys.readouterr().out
    assert not transport.commands("codex")
    assert not transport.commands("git", "fetch")
    assert not transport.commands("gh", "issue", "comment")
    assert not bridge.state.path.exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_cli_once_returns_failure_status(setup, exit_code):
    bridge, transport = setup
    transport.pytest_exit = exit_code
    python = bridge.python
    python.parent.mkdir(parents=True)
    python.touch()
    assert ab.main(["--issue", "1", "--once"]) == exit_code


def test_transport_uses_argv_stdin_and_never_shell(tmp_path, monkeypatch):
    captured = {}
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)
        def communicate(self, **kwargs):
            captured.update(kwargs)
            return "stdout", "stderr"
    monkeypatch.setattr(ab.subprocess, "Popen", Process)
    result = ab.run(["codex", "exec", "-"], tmp_path, input="$(fictional)")
    assert result.stdout == "stdout" and result.stderr == "stderr"
    assert captured["shell"] is False and captured["input"] == "$(fictional)"
    assert captured["cwd"] == tmp_path and captured["start_new_session"] is True


@pytest.mark.parametrize("interrupt", [subprocess.TimeoutExpired("codex", 1), KeyboardInterrupt()])
def test_transport_terminates_child_group_on_timeout_or_interrupt(tmp_path, monkeypatch, interrupt):
    kills = []
    class Process:
        pid = 999999
        def __init__(self, *_args, **_kwargs):
            self.calls = 0
        def communicate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise interrupt
            return "", ""
    monkeypatch.setattr(ab.subprocess, "Popen", Process)
    monkeypatch.setattr(ab.os, "killpg", lambda *args: kills.append(args))
    if isinstance(interrupt, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            ab.run(["codex", "exec"], tmp_path)
    else:
        assert ab.run(["codex", "exec"], tmp_path).returncode == 124
    assert kills == [(999999, ab.signal.SIGTERM)]


@pytest.mark.parametrize("filename", [".env", ".env.production", "data/fictional.json", "output/report.html",
                                    "auth.json", "Cookies", "browser_profiles/profile", "fictional.db",
                                    "fictional.sqlite3", "fictional.pem", ".codex/config.toml",
                                    "storage_state.json", "capture.har", "../outside.txt"])
def test_sensitive_or_external_files_cannot_be_published(setup, filename):
    bridge, transport = setup
    transport.staged_files = [filename]
    bridge.poll()
    assert bridge.state.data["status"] == "failed"
    assert not transport.commands("git", "commit")
    assert not transport.commands("git", "push")
    assert "Sensitive/generated/external file" in transport.commands("gh", "issue", "comment")[-1][2]["input"]


def test_paginated_comments_work_with_older_gh_without_slurp(setup):
    bridge, transport = setup
    transport.pages[1] = [[comment(owner="stranger")], [comment(11)]]
    bridge.poll()
    assert bridge.state.data["processed"] == [11]
    api_command = transport.commands("gh", "api")[0][0]
    assert "--paginate" in api_command and "--slurp" not in api_command
    assert len(transport.commands("codex", "exec")) == 1


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_persisted_pr_closure_blocks_review_before_model_or_push(setup, state):
    bridge, transport = setup
    bridge.poll()
    transport.prs[0]["state"] = state
    transport.comments[12] = [comment(11, "REVIEW", source=12)]
    bridge.state = ab.State(bridge.state.directory, 1)
    with pytest.raises(ab.BridgeError, match="closed or merged"):
        bridge.poll()
    assert len(transport.commands("codex", "exec")) == 1
    assert len(transport.commands("git", "push")) == 1
    assert bridge.state.data["processed"] == [10]


def test_fork_pr_with_matching_branch_is_not_associated(setup):
    bridge, transport = setup
    transport.prs = [{"number": 99, "url": "fictional", "state": "OPEN", "isCrossRepository": True}]
    bridge.poll()
    assert transport.commands("gh", "pr", "create")
    assert bridge.state.data["pr"]["number"] == 12
