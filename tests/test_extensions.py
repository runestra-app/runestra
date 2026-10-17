import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aorch.config import snapshot
from aorch.config.service import RunnerConfig
from aorch.extensions import devices
from aorch.extensions import git as git_extension
from aorch.extensions.git import git_action
from aorch.extensions.jira import jira_action, plain_text
from aorch.runner.agents import build_prompt, credential_source
from aorch.runner.api_agent import run_api_agent
from aorch.runner.executor import (
    environment,
    execute,
    project_environment_context,
    project_secret_names_context,
)
from aorch.runner.journal import Journal


def test_git_verify_preserves_structured_failure_and_diagnostic_artifact(tmp_path, monkeypatch):
    workspace = tmp_path / "run"
    code = workspace / "code"
    code.mkdir(parents=True)
    (workspace / "workspace.json").write_text(json.dumps({"branch": "codex/test"}))
    log_path = code / "output" / "verify.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("xcodebuild test failure details")
    log_data = log_path.read_bytes()
    structured_result = json.dumps({
        "status": "failed",
        "summary": "Unit tests failed",
        "error": "TestFailure",
        "outputs": {"checks": [{"exit_code": 65}]},
        "artifacts": [{
            "path": "output/verify.log",
            "sha256": hashlib.sha256(log_data).hexdigest(),
            "size": len(log_data),
        }],
    })

    async def fake_command(argv, cwd, config, journal, assignment, cancelled, timeout=300):
        if argv[0] == "python3":
            return structured_result
        if argv[1] == "write-tree":
            return "tree"
        if argv[1] == "rev-parse":
            return "a" * 40
        return ""

    monkeypatch.setattr("aorch.extensions.git.command", fake_command)
    async def scenario():
        return await git_action(
            "git.verify",
            {"commands": [["python3", "verify.py"]]},
            {},
            {"step": {"timeout_seconds": 60}, "attempt_id": "attempt", "run_id": "run"},
            SimpleNamespace(workspace_root=tmp_path),
            Journal(tmp_path / "journal.sqlite"),
            asyncio.Event(),
            workspace,
        )

    result = asyncio.run(scenario())
    assert result.status == "failed"
    assert result.summary == "Unit tests failed"
    assert result.outputs["checks"][0]["exit_code"] == 65
    assert result.outputs["verification_commands_not_run"] == []
    assert result.artifacts[0].path == "code/output/verify.log"
    assert result.artifacts[0].size == len(log_data)


def settings(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    return RunnerConfig(
        id="runner",
        token_file=tmp_path / "unused",
        workspace_root=root,
        journal=tmp_path / "journal.sqlite",
        api_credentials={"openai": "API_TEST_KEY"},
        allow_file_git_sources=True,
    )


def test_agent_prompt_references_cannot_escape_package(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with pytest.raises(ValueError, match="inside the workflow package"):
        build_prompt(
            {"step": {"prompt": "../../outside.txt", "skills": []}},
            package_dir,
            {},
        )


def test_build_prompt_accepts_inline_prompt_text(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    prompt = build_prompt(
        {
            "step": {
                "prompt_text": "Review the changes.",
                "skills": [],
            }
        },
        package_dir,
        {},
    )
    assert prompt.startswith("Review the changes.")


def test_build_prompt_still_reads_prompt_file(tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "review.md").write_text("Review the package changes.")
    prompt = build_prompt(
        {"step": {"prompt": "review.md", "skills": []}},
        package_dir,
        {},
    )
    assert prompt.startswith("Review the package changes.")


def test_jira_discovery_and_comment_replay(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_TEST_TOKEN", raising=False)
    settings(tmp_path)
    connections = {
        "jira": {
            "url": "https://example.atlassian.net",
            "email": "test@example.com",
            "token_env": "JIRA_TEST_TOKEN",
        }
    }
    posted = []

    def handler(request):
        if request.url.path.endswith("search/jql"):
            return httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {"summary": "Ticket", "description": "Details"},
                        }
                    ],
                    "isLast": True,
                },
            )
        if request.method == "GET":
            return httpx.Response(200, json={"comments": posted, "total": len(posted)})
        body = json.loads(request.content)
        posted.append({"id": "123", "body": body["body"]})
        return httpx.Response(201, json={"id": "123"})

    async def scenario():
        transport = httpx.MockTransport(handler)
        result = await jira_action(
            "jira.discover",
            {"connection": "jira", "assignee": "user", "status": "Ready"},
            connections,
            asyncio.Event(),
            tmp_path,
            transport,
        )
        assert result.outputs["work_items"][0]["id"] == "TEST-1"
        inputs = {
            "connection": "jira",
            "issue": "TEST-1",
            "ambiguities": [{"id": "scope", "question": "Which screen?"}],
        }
        for _ in range(2):
            assert (
                await jira_action(
                    "jira.comment", inputs, connections, asyncio.Event(), tmp_path, transport
                )
            ).status == "succeeded"
        assert len(posted) == 1
        failure = {
            "connection": "jira",
            "issue": "TEST-1",
            "run_id": "run-123",
            "failed_step": "verify",
            "status": "failed",
            "summary": "Unit tests failed",
            "error": "credential secret",
            "attempt_id": "attempt-123",
            "diagnostics": {"result": "Failed"},
            "artifacts": [{"path": "artifact-123", "size": 256}],
        }
        for _ in range(2):
            assert (
                await jira_action(
                    "jira.report_failure", failure, connections, asyncio.Event(), tmp_path, transport
                )
            ).status == "succeeded"
        assert len(posted) == 2
        report_text = plain_text(posted[-1]["body"])
        assert "artifact-123" in report_text
        assert "[aorch:failure:run-123]" in report_text
        assert "[REDACTED]" in report_text
        assert "secret" not in report_text

    project_values = project_environment_context.set({"JIRA_TEST_TOKEN": "secret"})
    secret_names = project_secret_names_context.set(frozenset({"JIRA_TEST_TOKEN"}))
    try:
        asyncio.run(scenario())
    finally:
        project_secret_names_context.reset(secret_names)
        project_environment_context.reset(project_values)


def test_jira_uses_project_secret_without_passing_it_to_subprocesses(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_TEST_TOKEN", raising=False)
    config = settings(tmp_path)
    connections = {
        "jira": {
            "url": "https://example.atlassian.net",
            "email": "test@example.com",
            "token_env": "JIRA_TEST_TOKEN",
        }
    }
    project_values = project_environment_context.set(
        {"JIRA_TEST_TOKEN": "project-secret", "PUBLIC_ENDPOINT": "https://example.test"}
    )
    secret_names = project_secret_names_context.set(frozenset({"JIRA_TEST_TOKEN"}))
    try:
        child_environment = environment(config)
        assert "JIRA_TEST_TOKEN" not in child_environment
        assert child_environment["PUBLIC_ENDPOINT"] == "https://example.test"

        def handler(request):
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"issues": [], "isLast": True})

        result = asyncio.run(
            jira_action(
                "jira.discover",
                {"connection": "jira", "jql": "project = TEST"},
                connections,
                asyncio.Event(),
                tmp_path,
                httpx.MockTransport(handler),
            )
        )
        assert result.status == "succeeded"
    finally:
        project_secret_names_context.reset(secret_names)
        project_environment_context.reset(project_values)


def test_jira_does_not_treat_project_variable_as_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_TEST_TOKEN", raising=False)
    settings(tmp_path)
    connections = {
        "jira": {"url": "https://example.atlassian.net", "token_env": "JIRA_TEST_TOKEN"}
    }
    project_values = project_environment_context.set({"JIRA_TEST_TOKEN": "plain-variable"})
    secret_names = project_secret_names_context.set(frozenset())
    try:
        with pytest.raises(ValueError, match="Missing Jira credential"):
            asyncio.run(
                jira_action(
                    "jira.discover",
                    {"connection": "jira", "jql": "project = TEST"},
                    connections,
                    asyncio.Event(),
                    tmp_path,
                )
            )
    finally:
        project_secret_names_context.reset(secret_names)
        project_environment_context.reset(project_values)


def test_api_agent_actually_uses_workspace_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TEST_KEY", "secret")
    config = settings(tmp_path)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        call = (
            {
                "type": "function_call",
                "call_id": str(len(calls)),
                "name": "write",
                "arguments": json.dumps({"path": "result.txt", "content": "verified"}),
            }
            if len(calls) == 1
            else {
                "type": "function_call",
                "call_id": "final",
                "name": "finish",
                "arguments": json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "done",
                        "outputs_json": "{}",
                        "workspace_revision": None,
                    }
                ),
            }
        )
        return httpx.Response(
            200,
            json={
                "output": [call],
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            },
        )

    profile = {
        "model": "test",
        "credential_env": "API_TEST_KEY",
        "tools": ["read", "write"],
        "max_iterations": 3,
        "max_total_tokens": 10000,
        "max_output_tokens": 128,
        "max_budget_usd": 1,
        "input_price_per_million": 1,
        "output_price_per_million": 1,
    }
    assert credential_source("openai", profile, config) == "API_TEST_KEY"
    with pytest.raises(ValueError, match="must match"):
        credential_source(
            "openai", {**profile, "credential_env": "UNRELATED_SECRET"}, config
        )
    config.environment = ["API_TEST_KEY"]
    assert "API_TEST_KEY" not in environment(config)
    config.connections = {
        "jira": {"url": "https://example.atlassian.net", "token_env": "JIRA_TEST_TOKEN"}
    }
    config.environment.append("JIRA_TEST_TOKEN")
    monkeypatch.setenv("JIRA_TEST_TOKEN", "jira-secret")
    assert "JIRA_TEST_TOKEN" not in environment(config)
    result = asyncio.run(
        run_api_agent(
            profile,
            "Write a file",
            tmp_path,
            10,
            asyncio.Event(),
            config,
            httpx.MockTransport(handler),
        )
    )
    assert result.status == "succeeded"
    assert (tmp_path / "result.txt").read_text() == "verified"
    assert calls[1]["input"][-1]["type"] == "function_call_output"


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_device_action_runs_commands_and_captures_artifacts(
    tmp_path, monkeypatch, platform
):
    config = settings(tmp_path)
    name = "test-device"
    identifier = "simulator-1" if platform == "ios" else "emulator-5554"
    config.devices = {name: {"platform": platform, "id": identifier}}
    workspace = config.workspace_root / "run"
    workspace.mkdir()
    assignment = {
        "attempt_id": "attempt",
        "run_id": "run",
        "step": {"resources": [name], "timeout_seconds": 3},
    }
    journal = Journal(config.journal)
    calls = []
    monkeypatch.setattr(devices.shutil, "which", lambda executable: executable)

    async def command(
        argv,
        cwd,
        runner_config,
        current_journal,
        current_assignment,
        cancelled,
        timeout=300,
    ):
        calls.append(argv)
        if argv[:4] == ["xcrun", "simctl", "list", "devices"]:
            return json.dumps(
                {
                    "devices": {
                        "runtime": [
                            {
                                "udid": identifier,
                                "isAvailable": True,
                                "state": "Shutdown",
                            }
                        ]
                    }
                }
            )
        if argv[:2] == ["adb", "devices"]:
            return f"List of devices attached\n{identifier}\tdevice\n"
        if "getprop" in argv:
            return "1"
        if argv[1:3] == ["simctl", "io"] and argv[4] == "screenshot":
            (cwd / f"{name}-screenshot.png").write_bytes(b"ios screenshot")
        if "pull" in argv:
            from pathlib import Path

            Path(argv[-1]).write_bytes(b"android screenshot")
        return ""

    monkeypatch.setattr(devices, "command", command)
    result = asyncio.run(
        devices.test_device(
            {"device": name, "commands": [["pytest", "-q", "@device"]]},
            assignment,
            config,
            journal,
            asyncio.Event(),
            workspace,
        )
    )

    assert result.status == "succeeded"
    assert result.artifacts
    assert any(identifier in argv for argv in calls)
    if platform == "ios":
        assert any(argv[:3] == ["xcrun", "simctl", "shutdown"] for argv in calls)
        assert journal.active_resources("attempt") == []


def test_git_worktree_commit_push_and_verification(tmp_path):
    config = settings(tmp_path)
    repository = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repository / "file.txt").write_text("original")
    git("add", ".")
    git("commit", "-m", "initial")
    git("init", "--bare", str(remote))
    git("remote", "add", "origin", str(remote))
    config.repositories = {"test": repository}
    journal = Journal(config.journal)
    assignment = {
        "attempt_id": "attempt",
        "run_id": "run",
        "step": {"timeout_seconds": 30},
    }
    workspace = config.workspace_root / "run"
    workspace.mkdir()

    async def action(name, inputs):
        return await git_action(
            name, inputs, {}, assignment, config, journal, asyncio.Event(), workspace
        )

    async def scenario():
        result = await action(
            "git.prepare", {"repository": "test", "item_id": "TEST-1"}
        )
        assert result.status == "succeeded"
        assert result.outputs["mode"] == "worktree"
        assert result.outputs["owner"] == "run"
        (workspace / "code/file.txt").write_text("updated")
        verified = await action("git.verify", {"commands": [["git", "diff", "--check"]]})
        with pytest.raises(ValueError, match="Approval does not match"):
            await action("git.commit", {"message": "Update", "approval_fingerprint": "0" * 64})
        committed = await action(
            "git.commit",
            {"message": "Update", "approval_fingerprint": verified.outputs["fingerprint"]},
        )
        assert (
            await action("git.commit", {"message": "Update"})
        ).workspace_revision == committed.workspace_revision
        await action("git.push", {})
        assert (
            git("ls-remote", "origin", result.outputs["branch"]).split()[0]
            == committed.workspace_revision
        )
        (workspace / "code/file.txt").write_text("changed after tests")
        with pytest.raises(ValueError, match="after verification"):
            await action("git.push", {})

    asyncio.run(scenario())


def test_executor_runs_pinned_checkout_branch_verify_commit_push_and_recovers_pr(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "README.md").write_text("pinned source\n")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(source), "push", "-u", "origin", "main"], check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    project = tmp_path / "project"
    config_dir = project / ".aorch"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "version: 3\nname: integration\nworkflows:\n  publish:\n    steps:\n"
        "      branch:\n        type: action\n        action: git.branch\n"
        "      write:\n        type: shell\n        needs: [branch]\n        command: \"printf 'created\\\\n' > note.txt\"\n"
        "      verify:\n        type: action\n        action: git.verify\n        needs: [write]\n"
        "        inputs:\n          commands: [[git, diff, --check, HEAD]]\n"
        "          paths: [note.txt]\n          only_paths: true\n"
        "      commit:\n        type: action\n        action: git.commit\n        needs: [verify]\n"
        "        inputs:\n          message: Add note\n          paths: [note.txt]\n"
        "      push:\n        type: action\n        action: git.push\n        needs: [commit]\n"
        "      pull_request:\n        type: action\n        action: github.open_pr\n        needs: [push]\n"
        "        inputs:\n          repository: example/project\n          base: main\n"
        "          title: Add note\n          body: Automated note\n"
        "  alternate:\n    steps:\n"
        "      prepare:\n        type: action\n        action: git.prepare\n"
        "        inputs:\n          repository: external\n          item_id: LOCAL-1\n"
        "      modify:\n        type: shell\n        needs: [prepare]\n"
        "        command: \"printf 'worktree\\\\n' > worktree-note.txt\"\n"
    )
    package = snapshot(config_dir / "config.yaml")
    package["repository"] = {"git": str(remote), "ref": "main", "commit": commit}
    payload = {key: value for key, value in package.items() if key != "sha256"}
    package["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    config = settings(tmp_path)
    config.repositories = {"external": source}
    journal = Journal(config.journal)
    results = {}
    pull_request = None
    lost_create_response = True
    original_command = git_extension.command

    async def fake_github(argv, cwd, runner_config, action_journal, assignment, cancelled, timeout=300):
        nonlocal pull_request, lost_create_response
        if argv[0] != "gh":
            return await original_command(
                argv, cwd, runner_config, action_journal, assignment, cancelled, timeout
            )
        if argv[1:3] == ["pr", "list"]:
            return json.dumps([pull_request] if pull_request else [])
        if argv[1:3] == ["pr", "create"]:
            body_path = Path(argv[argv.index("--body-file") + 1])
            pull_request = {
                "url": "https://github.com/example/project/pull/7",
                "number": 7,
                "body": body_path.read_text(),
                "headRefOid": results["commit"]["outputs"]["commit"],
                "baseRefName": "main",
                "state": "OPEN",
            }
            if lost_create_response:
                lost_create_response = False
                raise ValueError("Simulated lost response after remote PR creation")
            return ""
        if argv[1:3] == ["pr", "view"]:
            return json.dumps(pull_request)
        raise AssertionError(f"Unexpected GitHub command: {argv}")

    monkeypatch.setattr(git_extension, "command", fake_github)

    async def scenario():
        for name in ("branch", "write", "verify", "commit", "push"):
            step = package["configuration"]["workflows"]["publish"]["steps"][name]
            result = await execute(
                {
                    "attempt_id": name,
                    "run_id": "integration-run",
                    "workflow_name": "publish",
                    "step": step,
                    "inputs": {},
                    "previous_results": results,
                    "package": package,
                },
                config,
                journal,
                asyncio.Event(),
            )
            assert result.status == "succeeded", result.model_dump()
            results[name] = result.model_dump(mode="json")
        assert results["branch"]["outputs"]["mode"] == "checkout"
        assert results["commit"]["outputs"]["commit"] == results["push"]["workspace_revision"]
        assert results["push"]["outputs"] == {
            "remote": "origin",
            "branch": results["branch"]["outputs"]["branch"],
            "commit": results["commit"]["outputs"]["commit"],
        }
        process = await asyncio.create_subprocess_exec(
            "git",
            "--git-dir",
            str(remote),
            "rev-parse",
            results["branch"]["outputs"]["branch"],
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        assert stdout.decode().strip() == results["commit"]["outputs"]["commit"]

        pr_step = package["configuration"]["workflows"]["publish"]["steps"]["pull_request"]
        assignment = {
            "attempt_id": "pull_request-first",
            "run_id": "integration-run",
            "workflow_name": "publish",
            "step": pr_step,
            "inputs": {},
            "previous_results": results,
            "package": package,
        }
        with pytest.raises(ValueError, match="lost response"):
            await execute(assignment, config, journal, asyncio.Event())
        assignment["attempt_id"] = "pull_request-retry"
        result = await execute(assignment, config, journal, asyncio.Event())
        assert result.status == "succeeded"
        assert result.outputs["number"] == 7
        assert result.outputs["pull_request"].endswith("/pull/7")
        assert result.outputs["head_commit"] == results["commit"]["outputs"]["commit"]

        alternative_results = {}
        for name in ("prepare", "modify"):
            step = package["configuration"]["workflows"]["alternate"]["steps"][name]
            result = await execute(
                {
                    "attempt_id": f"alternate-{name}",
                    "run_id": "external-run",
                    "workflow_name": "alternate",
                    "step": step,
                    "inputs": {},
                    "previous_results": alternative_results,
                    "package": package,
                },
                config,
                journal,
                asyncio.Event(),
            )
            assert result.status == "succeeded", result.model_dump()
            alternative_results[name] = result.model_dump(mode="json")
        assert alternative_results["prepare"]["outputs"]["mode"] == "worktree"
        assert (config.workspace_root / "external-run" / "code" / "worktree-note.txt").is_file()

    asyncio.run(scenario())
