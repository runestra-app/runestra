"""Codex and Claude CLI adapters with explicit authentication and output contracts."""

import asyncio
import json
import os
import shutil
from pathlib import PurePosixPath

from aorch.contracts import StepResult
from aorch.runner.agent_scratch import (
    create_attempt_scratch,
    sandbox_denial,
    scratch_environment,
    validate_additional_writable_roots,
)
from aorch.runner.executor import environment, process_identity
from aorch.runner.process import log_context, log_suppressed, run_process

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["succeeded", "needs_input", "failed", "needs_attention"],
        },
        "summary": {"type": "string"},
        "outputs_json": {
            "type": "string",
            "description": "JSON object with task outputs, including decision and ambiguities for analysis",
        },
        "workspace_revision": {"type": ["string", "null"]},
    },
    "required": ["status", "summary", "outputs_json", "workspace_revision"],
    "additionalProperties": False,
}
AGENT_PROCESS_OUTPUT_LIMIT = 4 * 1024 * 1024


def credential_source(provider, profile, config):
    configured = config.api_credentials.get(provider)
    requested = profile.get("credential_env")
    if requested and requested != configured:
        raise ValueError(
            f"Profile credential_env must match the runner's configured {provider} credential"
        )
    if not configured:
        raise ValueError(f"No runner API credential is configured for {provider}")
    return configured


def parse_agent_result(data):
    outputs = json.loads(data["outputs_json"])
    return StepResult(
        status=data["status"],
        summary=data["summary"],
        outputs=outputs,
        workspace_revision=data["workspace_revision"],
    )


def build_codex_arguments(
    executable, profile, scratch, writable_roots, schema_file, result_file,
    trusted_host=False,
):
    arguments = [
        executable,
        "exec",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--json",
    ]
    if trusted_host:
        arguments.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        arguments.extend(("--sandbox", profile["sandbox"]))
    if not trusted_host and profile["sandbox"] == "workspace-write":
        arguments.extend(("--add-dir", str(scratch)))
    if not trusted_host:
        for root in writable_roots:
            arguments.extend(("--add-dir", str(root)))
    arguments.extend(
        (
            "--output-schema",
            str(schema_file),
            "--output-last-message",
            str(result_file),
            "-",
        )
    )
    if profile.get("model"):
        arguments.extend(("--model", profile["model"]))
    return arguments


def build_claude_arguments(executable, profile, trusted_host=False):
    tool_map = {"read": "Read,Grep,Glob", "write": "Edit,Write", "command": "Bash"}
    tools = ",".join(tool_map[tool] for tool in profile["tools"])
    arguments = [
        executable,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(RESULT_SCHEMA),
        "--permission-mode",
        "bypassPermissions" if trusted_host else "dontAsk",
        "--permission-prompts",
        "none",
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--max-budget-usd",
        str(profile["max_budget_usd"]),
    ]
    if trusted_host:
        arguments.append("--dangerously-skip-permissions")
    return arguments


def sandbox_denial_result(denial, tool):
    path = denial["path"]
    summary = f"Sandbox denied {tool} writing {path or 'an unknown path'}: {denial['message']}"
    return StepResult(
        status="failed",
        summary=summary,
        outputs={"denied_path": path, "tool": tool},
        error="AgentSandboxDenied",
    )


def _sandbox_denied_tool(stdout):
    tool = "Codex CLI"
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item", {})
        if not isinstance(item, dict):
            continue
        if item.get("type") == "command_execution":
            command = item.get("command", "").strip()
            tool = command.split(maxsplit=1)[0] if command else "Codex command execution"
    return tool


def build_prompt(assignment, package_dir, context):
    step = assignment["step"]
    root = package_dir.resolve()

    def package_file(reference, allow_directory=False):
        relative = PurePosixPath(reference)
        path = (root / reference).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root):
            raise ValueError("Agent prompt and skill references must stay inside the workflow package")
        if not path.is_file() and not (
            allow_directory and path.is_dir() and (path / "SKILL.md").is_file()
        ):
            raise ValueError("Agent prompt and skill references must name packaged files")
        return path

    prompt = (
        step["prompt_text"]
        if step.get("prompt_text") is not None
        else package_file(step["prompt"]).read_text()
    )
    parts = [prompt]
    for reference in step["skills"]:
        path = package_file(reference, allow_directory=True)
        instruction = path / "SKILL.md" if path.is_dir() else path
        parts.append(f"Skill instructions from {reference}:\n{instruction.read_text()}")
    parts.append("Execution context:\n" + json.dumps(context))
    parts.append(
        "Return the declared result schema. outputs_json must encode an object. Do not commit, push, comment or publish; those are separate workflow actions."
    )
    parts.append(
        "Temporary files, tool caches, generated source, compiler caches, and build output must use "
        "RUNESTRA_ATTEMPT_SCRATCH or the provided TMPDIR, TMP, TEMP, XDG_CACHE_HOME, and "
        "tool-specific cache/build environment paths whenever the tool supports redirection. "
        "For tools that require command-line settings, point compiler module caches and build "
        "output options at RUNESTRA_ATTEMPT_SCRATCH. Keep intended source changes in the workspace. "
        "If a tool cannot redirect a write and "
        "the sandbox denies it, report the exact denied path and tool. Do not retry by broadening "
        "filesystem access or changing sandbox settings."
    )
    if context.get("agent_execution") == "trusted-host":
        parts.append(
            "This runner is configured for trusted-host execution. Your shell and file tools "
            "can access files allowed to the runner's operating-system account. Treat repository "
            "scripts and build phases as executable code, and do not access unrelated user data."
        )
    if context.get("coding_repair_role") == "review":
        parts.append(
            "Coding repair review output contract: outputs_json must be a JSON object "
            'with an "approved" boolean and a "findings" array. Set approved to true '
            "when the requested acceptance criteria are met and no material finding "
            'blocks the change. In particular, "No material findings" means approved '
            "is true. Set approved to false only when a concrete material issue or an unmet "
            "acceptance criterion blocks approval. Low-severity suggestions do not block "
            "approval unless an acceptance criterion requires them. Keep any limitations "
            "of the review in the summary or findings."
        )
    return "\n\n".join(parts)


async def execute_agent(
    assignment, config, journal, cancelled, workspace, package_dir, context
):
    context = {**context, "agent_execution": config.agent_execution}
    profile = assignment["package"]["configuration"]["profiles"][
        assignment["step"]["profile"]
    ]
    adapter = profile["adapter"]
    prompt = build_prompt(assignment, package_dir, context)
    if adapter == "openai":
        from aorch.runner.api_agent import run_api_agent

        return await run_api_agent(
            profile,
            prompt,
            workspace,
            assignment["step"]["timeout_seconds"],
            cancelled,
            config,
        )
    executable = shutil.which(adapter)
    if executable is None:
        raise ValueError(f"Missing agent CLI: {adapter}")
    env = environment(config, profile["authentication"])
    if profile["authentication"] == "api":
        provider = "codex" if adapter == "codex" else "claude"
        source = credential_source(provider, profile, config)
        if not os.environ.get(source):
            raise ValueError(f"Missing credential environment variable: {source}")
        target = "CODEX_API_KEY" if adapter == "codex" else "ANTHROPIC_API_KEY"
        env[target] = os.environ[source]
    writable_roots = []
    trusted_host = config.agent_execution == "trusted-host"
    if adapter == "codex" and not trusted_host:
        writable_roots = validate_additional_writable_roots(
            profile.get("additional_writable_roots", []), workspace
        )
    scratch = create_attempt_scratch(workspace, assignment["attempt_id"])
    env.update(scratch_environment(scratch))
    result_file = scratch / "result.json"
    schema_file = package_dir / "result-schema.json"
    try:
        schema_file.write_text(json.dumps(RESULT_SCHEMA))
        if adapter == "codex":
            argv = build_codex_arguments(
                executable, profile, scratch, writable_roots, schema_file, result_file,
                trusted_host=trusted_host,
            )
        elif adapter == "claude":
            argv = build_claude_arguments(executable, profile, trusted_host)
        else:
            raise ValueError(f"Unsupported adapter: {adapter}")
        if adapter != "codex" and profile.get("model"):
            argv += ["--model", profile["model"]]
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    try:
        suppression_token = log_suppressed.set(True)
        try:
            code, stdout, stderr = await run_process(
                argv,
                workspace,
                env,
                prompt,
                assignment["step"]["timeout_seconds"],
                cancelled,
                lambda pid: journal.started(
                    assignment["attempt_id"], pid, process_identity(pid)
                ),
                limit=AGENT_PROCESS_OUTPUT_LIMIT,
            )
        finally:
            log_suppressed.reset(suppression_token)
        denial = sandbox_denial(stderr + "\n" + stdout)
        tool = _sandbox_denied_tool(stdout) if denial and adapter == "codex" else adapter
        if denial:
            logger = log_context.get()
            if logger:
                logger("sandbox.denied", {
                    "message": f"Sandbox denied {tool} writing {denial['path'] or 'an unknown path'}: {denial['message']}",
                    "path": denial["path"],
                    "tool": tool,
                })
        if code:
            diagnostic = "\n".join(stderr.splitlines()[-8:]).strip()[-1200:]
            if not diagnostic:
                for line in reversed(stdout.splitlines()):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "result" and isinstance(event.get("result"), str):
                        diagnostic = event["result"].strip()[-1200:]
                        break
            summary = f"{adapter} exited with {code}"
            if denial:
                denial["tool"] = tool
                failure = sandbox_denial_result(denial, tool)
                return failure
            if diagnostic:
                summary += f": {diagnostic}"
            return StepResult(
                status="failed",
                summary=summary,
                outputs={},
                error="AgentRuntimeError",
            )
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        logger = log_context.get()
        if logger:
            for event in events:
                event_type = event.get("type", "agent.event")
                safe = {key: event[key] for key in ("name", "tool_name", "exit_code") if key in event}
                if event_type == "rate_limit_event":
                    info = event.get("rate_limit_info")
                    if isinstance(info, dict):
                        safe["rate_limit_info"] = {
                            key: info[key]
                            for key in (
                                "status",
                                "rateLimitType",
                                "rateLimitBucket",
                                "resetsAt",
                            )
                            if key in info
                            and isinstance(info[key], (str, int, float, bool))
                        }
                logger("agent", {"message": f"{adapter}: {event_type}", "data": safe})
        if adapter == "codex":
            data = json.loads(result_file.read_text())
        else:
            final = next(
                event for event in reversed(events) if event.get("type") == "result"
            )
            if final.get("is_error"):
                raise ValueError(str(final.get("result", "Claude runtime failed")))
            data = final["structured_output"]
        result = parse_agent_result(data)
        if denial and result.status == "failed":
            failure = sandbox_denial_result(denial, tool)
            result.summary = failure.summary
            result.error = failure.error
            result.outputs.update(failure.outputs)
        return result
    except asyncio.CancelledError:
        return StepResult(status="cancelled", summary="Agent stopped")
    finally:
        result_file.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
