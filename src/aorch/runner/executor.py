"""Explicit action and shell execution; agent adapters are selected by profile."""

import asyncio
import json
import os
import subprocess
import tempfile
from contextvars import ContextVar

from jsonschema import validate

from aorch.contracts import StepResult
from aorch.extensions.schemas import ACTION_REGISTRY
from aorch.runner.git import git_environment
from aorch.runner.package import materialize
from aorch.runner.process import run_process

project_environment_context = ContextVar("aorch_project_environment", default=None)
project_secret_names_context = ContextVar("aorch_project_secret_names", default=frozenset())


def project_secret(name):
    """Return a project secret value, or None when the name is not a secret."""
    if name not in project_secret_names_context.get():
        return None
    return (project_environment_context.get() or {}).get(name)


def process_identity(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def environment(config, authentication="local"):
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "TMPDIR",
        "SHELL",
        "SSH_AUTH_SOCK",
        *config.environment,
    }
    blocked = set(config.api_credentials.values())
    blocked.update(
        connection["token_env"]
        for connection in config.connections.values()
        if "token_env" in connection
    )
    blocked.update(project_secret_names_context.get())
    blocked.update(config.redact_env)
    if authentication == "local":
        blocked.update({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_API_KEY"})
    base = {
        key: value
        for key, value in os.environ.items()
        if key in allowed and key not in blocked and not key.startswith("AORCH_")
    }
    for key, value in (project_environment_context.get() or {}).items():
        if (
            key not in blocked
            and key not in {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "SHELL"}
            and not key.startswith("AORCH_")
        ):
            base[key] = value
    return base


async def prepare_repository(repository, workspace, config, cancelled):
    code_dir = workspace / "code"
    marker = workspace / ".aorch-commit"
    workspace_state = workspace / "workspace.json"
    current_marker = marker.read_text().strip() if marker.is_file() else None
    if current_marker == repository["commit"]:
        return code_dir
    if current_marker and workspace_state.is_file():
        try:
            saved = json.loads(workspace_state.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid workspace metadata: {error}") from error
        if saved.get("mode") == "checkout":
            branch = saved.get("branch")
            if not isinstance(branch, str) or not branch:
                raise ValueError(
                    "Existing checkout workspace has no saved branch; "
                    "preserve it and inspect workspace metadata before retrying"
                )

            if saved.get("repository") != repository.get("git"):
                raise ValueError(
                    "Saved workflow branch belongs to a different repository than "
                    "the current project snapshot. Inspect the workspace before retrying."
                )

            with tempfile.TemporaryDirectory(prefix="runestra-git-auth-") as auth_dir:
                git_env = git_environment(
                    environment(config),
                    repository["git"],
                    auth_dir,
                    allow_file=config.allow_file_git_sources,
                    project_names=(project_environment_context.get() or {}).keys(),
                )

                async def git_checkout_command(argv):
                    code, stdout, stderr = await run_process(
                        ["git", "-C", str(code_dir), *argv],
                        workspace,
                        git_env,
                        "",
                        180,
                        cancelled,
                    )
                    if code:
                        detail = (stderr or stdout).strip()[:1000]
                        raise RuntimeError(detail or f"Git command failed: {' '.join(argv)}")
                    return stdout.strip()

                try:
                    await git_checkout_command(
                        ["remote", "set-url", "origin", repository["git"]]
                    )
                    await git_checkout_command(
                        [
                            "fetch",
                            "--no-tags",
                            "origin",
                            repository.get("ref", "HEAD"),
                            repository["commit"],
                        ]
                    )
                    await git_checkout_command(
                        ["cat-file", "-e", f"{repository['commit']}^{{commit}}"]
                    )
                except RuntimeError as error:
                    raise RuntimeError(
                        f"Could not fetch project commit {repository['commit']} "
                        f"from the configured repository ref: {error}"
                    ) from error

                current_branch = await git_checkout_command(["branch", "--show-current"])
                if current_branch != branch:
                    try:
                        await git_checkout_command(["switch", branch])
                    except RuntimeError as error:
                        raise RuntimeError(
                            f"Could not restore saved workflow branch '{branch}' "
                            f"after the project commit changed: {error}"
                        ) from error

                head = await git_checkout_command(["rev-parse", "HEAD"])
                if head != repository["commit"]:
                    try:
                        if head == saved.get("starting_commit"):
                            # No commits were made on this branch, so advance its base
                            # while preserving any non-conflicting working tree files.
                            await git_checkout_command(
                                ["reset", "--keep", repository["commit"]]
                            )
                        else:
                            await git_checkout_command(
                                ["merge", "--ff-only", repository["commit"]]
                            )
                    except RuntimeError as error:
                        raise RuntimeError(
                            f"Saved workflow branch '{branch}' could not be advanced "
                            f"safely to project commit {repository['commit']}. "
                            "No forced reset was performed. Inspect the branch and "
                            f"merge or rebase it manually: {error}"
                        ) from error
                    head = await git_checkout_command(["rev-parse", "HEAD"])
                if head == repository["commit"] and saved.get("starting_commit") != head:
                    saved["starting_commit"] = repository["commit"]
                    workspace_state.write_text(json.dumps(saved))
                marker.write_text(repository["commit"])
                return code_dir

    with tempfile.TemporaryDirectory(prefix="runestra-git-auth-") as auth_dir:
        git_env = git_environment(
            environment(config), repository["git"], auth_dir,
            allow_file=config.allow_file_git_sources,
            project_names=(project_environment_context.get() or {}).keys(),
        )

        async def git(argv):
            code, stdout, stderr = await run_process(
                argv, workspace, git_env, "", 180, cancelled,
            )
            if code:
                raise RuntimeError(f"Git checkout failed: {stderr or stdout}")

        if not (code_dir / ".git").is_dir():
            code_dir.mkdir(parents=True, exist_ok=True)
            await git(["git", "init", str(code_dir)])
            await git(["git", "-C", str(code_dir), "remote", "add", "origin", repository["git"]])
        else:
            await git(["git", "-C", str(code_dir), "remote", "set-url", "origin", repository["git"]])
        await git(
            [
                "git",
                "-C",
                str(code_dir),
                "fetch",
                "--depth",
                "1",
                "origin",
                repository.get("ref", "HEAD"),
                repository["commit"],
            ]
        )
        await git(["git", "-C", str(code_dir), "checkout", "--force", "--detach", repository["commit"]])
    marker.write_text(repository["commit"])
    return code_dir


async def _execute(assignment, config, journal, cancelled):
    step = assignment["step"]
    from aorch.runner.process import log_context

    logger = log_context.get()
    if logger:
        logger("operation.started", {"message": f"Executing {step.get('action') or step['type']}", "source": "executor"})
    workspace = config.workspace_root / assignment.get(
        "workspace_key", assignment["run_id"]
    )
    workspace.mkdir(parents=True, exist_ok=True)
    repository = assignment["package"].get("repository")
    custom_actions = assignment["package"]["configuration"].get("actions", {})
    action_name = step.get("action")
    uses_repository_workspace = (
        action_name is None
        or action_name in custom_actions
        or ACTION_REGISTRY.get(action_name, {}).get("workspace", False)
    )
    workspace_state = workspace / "workspace.json"
    uses_external_worktree = False
    if workspace_state.is_file():
        try:
            uses_external_worktree = (
                json.loads(workspace_state.read_text()).get("mode", "worktree") == "worktree"
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid workspace metadata: {error}") from error
    if (
        repository
        and uses_repository_workspace
        and action_name != "git.prepare"
        and not uses_external_worktree
    ):
        await prepare_repository(repository, workspace, config, cancelled)
    package_dir = workspace / ".aorch-package"
    materialize(assignment["package"], package_dir)
    inputs = dict(step["inputs"])
    reference_context = {
        "inputs": assignment["inputs"],
        "steps": assignment.get("previous_results", {}),
        "failure": assignment.get("failure") or {},
    }
    for key, reference in step.get("input_refs", {}).items():
        value = reference_context
        for segment in reference.split("."):
            value = value[segment]
        inputs[key] = value
    context = {
        "inputs": assignment["inputs"],
        "step_inputs": inputs,
        "steps": assignment.get("previous_results", {}),
        "failure": assignment.get("failure") or {},
    }
    execution_dir = workspace / "code" if (workspace / "code").is_dir() else workspace
    custom = custom_actions.get(action_name)
    if custom:
        validate(inputs, custom["input_schema"])
        step = {
            **step,
            "argv": [
                str(package_dir / argument.removeprefix("@package/"))
                if argument.startswith("@package/")
                else argument
                for argument in custom["argv"]
            ],
            "action": None,
        }
        context["step_inputs"] = inputs
    if step.get("action"):
        from aorch.extensions import execute_action

        if step["action"] not in assignment["package"]["configuration"].get(
            "actions", {}
        ):
            from aorch.extensions.schemas import validate_action

            validate_action(step["action"], inputs, {})
        result = await execute_action(
            step["action"],
            inputs,
            context,
            assignment,
            config,
            journal,
            cancelled,
            workspace,
        )
        if step["action"] not in assignment["package"]["configuration"].get(
            "actions", {}
        ):
            from aorch.extensions.schemas import validate_action_result

            contract_version = assignment["package"].get("action_contracts", {}).get(
                assignment.get("workflow_name", ""), {}
            ).get(step["action"], 1)
            validate_action_result(step["action"], result, contract_version)
        return result
    if step["type"] == "agent":
        from aorch.runner.agents import execute_agent

        return await execute_agent(
            assignment, config, journal, cancelled, execution_dir, package_dir, context
        )
    argv = (
        step["argv"] if step["type"] == "action" else ["/bin/sh", "-c", step["command"]]
    )
    try:
        code, stdout, stderr = await run_process(
            argv,
            execution_dir,
            environment(config),
            json.dumps(context),
            step["timeout_seconds"],
            cancelled,
            lambda pid: journal.started(
                assignment["attempt_id"], pid, process_identity(pid)
            ),
        )
        if code:
            return StepResult(
                status="failed",
                summary=f"Command exited with {code}",
                outputs={"stdout": stdout, "stderr": stderr, "exit_code": code},
            )
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "status" in parsed and "summary" in parsed:
            if custom:
                validate(parsed, custom["result_schema"])
            return StepResult.model_validate(parsed)
        if custom:
            raise ValueError("Custom action must return a structured result")
        return StepResult(
            status="succeeded",
            summary="Command completed",
            outputs={"stdout": stdout, "stderr": stderr, "exit_code": code},
        )
    except asyncio.CancelledError:
        return StepResult(status="cancelled", summary="Execution stopped")
    except (OSError, ValueError, TimeoutError) as error:
        return StepResult(
            status="failed", summary=str(error), error=type(error).__name__
        )


async def execute(assignment, config, journal, cancelled):
    token = project_environment_context.set(assignment.get("project_environment", {}))
    secret_names_token = project_secret_names_context.set(
        frozenset(assignment.get("project_secret_names", ()))
    )
    try:
        return await _execute(assignment, config, journal, cancelled)
    finally:
        project_secret_names_context.reset(secret_names_token)
        project_environment_context.reset(token)
