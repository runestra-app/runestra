from pathlib import Path

import pytest

from aorch.runner.agent_scratch import (
    SCRATCH_ENVIRONMENT,
    create_attempt_scratch,
    sandbox_denial,
    scratch_environment,
    validate_additional_writable_roots,
)
from aorch.runner.agents import (
    build_claude_arguments,
    build_codex_arguments,
    sandbox_denial_result,
)


def test_attempt_scratch_is_private_and_isolated(tmp_path):
    workspace = tmp_path / "workspaces" / "run" / "code"
    workspace.mkdir(parents=True)
    first = create_attempt_scratch(workspace, "attempt-1")
    second = create_attempt_scratch(workspace, "attempt-2")

    assert first != second
    assert first.parent == second.parent
    assert first.stat().st_mode & 0o777 == 0o700
    assert first.parent.stat().st_mode & 0o777 == 0o700
    assert not first.is_relative_to(workspace)


def test_attempt_scratch_environment_redirects_common_caches(tmp_path):
    scratch = create_attempt_scratch(tmp_path / "runner" / "code", "attempt")
    environment = scratch_environment(scratch)

    assert environment["RUNESTRA_ATTEMPT_SCRATCH"] == str(scratch)
    assert set(SCRATCH_ENVIRONMENT).issubset(environment)
    for key in (
        "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "CARGO_TARGET_DIR",
        "GRADLE_USER_HOME", "SWIFTPM_MODULECACHE_OVERRIDE",
    ):
        assert Path(environment[key]).is_relative_to(scratch)
        assert Path(environment[key]).is_dir()


def test_additional_writable_roots_are_explicit_existing_narrow_directories(tmp_path):
    workspace = tmp_path / "workspace" / "code"
    workspace.mkdir(parents=True)
    extra = tmp_path / "tool-cache"
    extra.mkdir()

    assert validate_additional_writable_roots([str(extra)], workspace) == [extra.resolve()]
    with pytest.raises(ValueError, match="existing absolute directory"):
        validate_additional_writable_roots([str(tmp_path / "missing")], workspace)
    with pytest.raises(ValueError, match="must not include the runner workspace"):
        validate_additional_writable_roots([str(tmp_path)], workspace)
    with pytest.raises(ValueError, match="too broad"):
        validate_additional_writable_roots([str(Path.home())], workspace)


def test_codex_arguments_add_only_configured_roots_and_attempt_scratch(tmp_path):
    scratch = tmp_path / "scratch"
    extra = tmp_path / "extra"
    args = build_codex_arguments(
        "/usr/bin/codex",
        {"sandbox": "workspace-write", "model": "test-model"},
        scratch,
        [extra],
        tmp_path / "schema.json",
        tmp_path / "result.json",
    )

    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert args.count("--add-dir") == 2
    assert args[args.index("--add-dir") + 1] == str(scratch)
    assert args[args.index("--add-dir", args.index("--add-dir") + 1) + 1] == str(extra)
    assert args[args.index("--model") + 1] == "test-model"


def test_codex_read_only_does_not_add_attempt_scratch(tmp_path):
    args = build_codex_arguments(
        "codex", {"sandbox": "read-only"}, tmp_path / "scratch", [], "schema", "result"
    )
    assert "--add-dir" not in args


def test_trusted_host_codex_arguments_disable_cli_sandbox_without_extra_roots(tmp_path):
    args = build_codex_arguments(
        "codex", {"sandbox": "workspace-write"}, tmp_path / "scratch",
        [tmp_path / "extra"], "schema", "result", trusted_host=True,
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--sandbox" not in args
    assert "--add-dir" not in args


def test_trusted_host_claude_arguments_bypass_cli_permissions():
    args = build_claude_arguments(
        "claude", {"tools": ["read", "write", "command"], "max_budget_usd": 1},
        trusted_host=True,
    )
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--dangerously-skip-permissions" in args


def test_sandbox_denial_keeps_path_and_diagnostic_line():
    line = "Operation not permitted: /Users/example/project/.cache/tool/index"
    assert sandbox_denial(f"ordinary output\n{line}") == {
        "message": line,
        "path": "/Users/example/project/.cache/tool/index",
    }
    quoted_line = "PermissionError: Operation not permitted: '/tmp/tool cache/index'"
    assert sandbox_denial(quoted_line)["path"] == "/tmp/tool cache/index"
    assert sandbox_denial("normal command failure") is None


def test_sandbox_denial_structured_failure_reports_path_and_tool():
    result = sandbox_denial_result(
        {"message": "Operation not permitted", "path": "/outside/cache"}, "clang"
    )
    assert result.status == "failed"
    assert result.error == "AgentSandboxDenied"
    assert result.outputs == {"denied_path": "/outside/cache", "tool": "clang"}
    assert "/outside/cache" in result.summary
    assert "clang" in result.summary
