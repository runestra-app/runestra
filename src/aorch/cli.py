"""Configuration tooling for Runestra."""

import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer

from aorch.config import ConfigurationError, snapshot
from aorch.config.models import (
    ActionDefinition,
    AgentProfile,
    JiraConnection,
    ProjectConfig,
    Workflow,
)
from aorch.contracts import Lease, StepResult
from aorch.extensions.schemas import ACTION_REGISTRY

app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config")
actions_app = typer.Typer(no_args_is_help=True)
app.add_typer(actions_app, name="actions")


@app.command()
def validate(path: Annotated[Path, typer.Argument()] = Path(".")):
    """Validate a project and every referenced prompt or skill."""
    try:
        result = snapshot(path)
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"Valid: {result['configuration']['name']} ({result['sha256']})")


@app.command()
def doctor(
    runner_config: Annotated[
        Path | None,
        typer.Option("--runner-config", help="Runner YAML to inspect."),
    ] = None,
    engine_url: Annotated[
        str | None,
        typer.Option(help="Override the engine URL from runner config."),
    ] = None,
):
    """Check local prerequisites and connectivity without printing secrets."""
    import shutil

    import httpx

    from aorch.config.service import RunnerConfig, load_service
    from aorch.contracts import PROTOCOL_VERSION

    problems = 0

    def report(label, ok, detail, required=True):
        nonlocal problems
        state = "OK" if ok else ("MISSING" if required else "OPTIONAL")
        typer.echo(f"{state}  {label}: {detail}")
        if not ok and required:
            problems += 1

    for executable in ("git", "curl"):
        report(executable, shutil.which(executable) is not None, "available" if shutil.which(executable) else "install it and reopen your terminal")

    docker_available = shutil.which("docker") is not None
    report(
        "Docker",
        docker_available,
        "available" if docker_available else "optional for direct Python installation",
        required=False,
    )

    settings = None
    if runner_config is not None:
        try:
            settings = load_service(runner_config.expanduser(), RunnerConfig)
            typer.echo(f"OK  runner config: {runner_config.expanduser().resolve()}")
        except (OSError, ValueError) as error:
            report("runner config", False, str(error))

    url = engine_url or (settings.engine_url if settings else None)
    if url:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
            if (
                not hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"})
            ):
                raise ValueError("Engine URL must be HTTPS or a loopback HTTP URL without credentials")
            netloc = f"[{hostname}]" if ":" in hostname else hostname
            if port:
                netloc += f":{port}"
            safe_url = parsed._replace(netloc=netloc).geturl()
            response = httpx.get(safe_url.rstrip("/") + "/health", timeout=5)
            response.raise_for_status()
            health = response.json()
            version = health.get("protocol_version")
            compatible = str(version) == PROTOCOL_VERSION
            report(
                "engine",
                compatible,
                f"reachable, protocol {version}; runner expects {PROTOCOL_VERSION}"
                if not compatible
                else f"reachable, protocol {version}",
            )
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, ValueError) and str(error).startswith("Engine URL"):
                report("engine", False, str(error))
            else:
                report("engine", False, f"not reachable; {type(error).__name__}")
    else:
        typer.echo("INFO  engine: pass --engine-url or --runner-config to check connectivity")

    if settings:
        token_file = settings.token_file
        try:
            token = token_file.read_text().strip()
            report("runner token", len(token) >= 32, "present" if len(token) >= 32 else "missing or invalid")
        except OSError:
            report("runner token", False, f"not readable at {token_file}")

    for adapter in ("codex", "claude"):
        executable = shutil.which(adapter)
        report(
            f"{adapter} CLI",
            executable is not None,
            f"available at {executable}" if executable else "optional; install and sign in to use this agent",
            required=False,
        )

    if problems:
        raise typer.Exit(1)


@app.command("agent-test")
def agent_test(adapter: Annotated[str, typer.Argument(help="Installed local CLI: codex or claude.")]):
    """Make a real, low-budget model call to verify the selected local CLI login."""
    import asyncio
    import os
    import shutil
    import tempfile

    from aorch.runner.agents import RESULT_SCHEMA
    from aorch.runner.process import run_process

    if adapter not in {"codex", "claude"}:
        typer.echo("Choose codex or claude.", err=True)
        raise typer.Exit(2)
    executable = shutil.which(adapter)
    if executable is None:
        typer.echo(f"Missing {adapter} CLI. Install it and complete its sign-in first.", err=True)
        raise typer.Exit(1)
    typer.echo("This performs a real provider request and may consume your plan or API quota.")
    if not typer.confirm("Send the test prompt now?", default=False):
        typer.echo("Cancelled.")
        return
    prompt = (
        "This is a connectivity check. Do not use tools or access files. "
        'Return status "succeeded", summary "Runestra agent check passed", '
        'outputs_json "{}", and workspace_revision null.'
    )
    with tempfile.TemporaryDirectory(prefix="runestra-agent-check-") as temporary:
        workspace = Path(temporary)
        schema_path = workspace / "result-schema.json"
        result_path = workspace / "result.json"
        schema_path.write_text(json.dumps(RESULT_SCHEMA))
        if adapter == "codex":
            argv = [
                executable, "exec", "--ignore-user-config", "--skip-git-repo-check",
                "--json", "--sandbox", "read-only", "--output-schema", str(schema_path),
                "--output-last-message", str(result_path), "-",
            ]
        else:
            tool_map = '{"type":"object","properties":{"status":{"type":"string","enum":["succeeded","needs_input","failed","needs_attention"]},"summary":{"type":"string"},"outputs_json":{"type":"string"},"workspace_revision":{"type":["string","null"]}},"required":["status","summary","outputs_json","workspace_revision"],"additionalProperties":false}'
            argv = [
                executable, "--print", "--output-format", "stream-json", "--verbose",
                "--json-schema", tool_map, "--permission-mode", "dontAsk",
                "--permission-prompts", "none", "--tools", "Read", "--allowedTools", "Read",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--setting-sources", "", "--max-budget-usd", "0.05",
            ]
        try:
            code, stdout, _ = asyncio.run(
                run_process(argv, workspace, os.environ.copy(), prompt, 120, asyncio.Event(), limit=1024 * 1024)
            )
        except (OSError, TimeoutError, ValueError) as error:
            typer.echo(f"{adapter} agent check failed: {type(error).__name__}", err=True)
            raise typer.Exit(1) from error
        if code:
            typer.echo(f"{adapter} agent check failed with exit code {code}.", err=True)
            raise typer.Exit(1)
        try:
            if adapter == "codex":
                result = json.loads(result_path.read_text())
            else:
                events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
                result = next(event["structured_output"] for event in reversed(events) if event.get("type") == "result")
            if result.get("status") != "succeeded":
                raise ValueError("The model returned an unsuccessful result")
        except (OSError, KeyError, StopIteration, json.JSONDecodeError, ValueError) as error:
            typer.echo(f"{adapter} CLI ran but did not return the expected result ({type(error).__name__}).", err=True)
            raise typer.Exit(1) from error
    typer.echo(f"{adapter} completed a real model call successfully. Usage reporting depends on the provider CLI.")


@app.command("snapshot")
def export_snapshot(
    path: Annotated[Path, typer.Argument()] = Path("."), output: Path | None = None
):
    """Export an immutable, hashed JSON configuration package."""
    try:
        result = snapshot(path)
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(content)
    else:
        typer.echo(content, nl=False)


@app.command()
def schema():
    """Print project configuration and protocol JSON schemas."""
    typer.echo(
        json.dumps(
            {
                "configuration": ProjectConfig.model_json_schema(),
                "modular_configuration": {
                    "type": "object",
                    "properties": {
                        "version": {"const": 3},
                        "name": {"type": "string", "minLength": 1},
                        "imports": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "profiles": {
                            "type": "object",
                            "additionalProperties": AgentProfile.model_json_schema(),
                        },
                        "actions": {
                            "type": "object",
                            "additionalProperties": ActionDefinition.model_json_schema(),
                        },
                        "connections": {
                            "type": "object",
                            "additionalProperties": JiraConnection.model_json_schema(),
                        },
                        "workflows": {
                            "type": "object",
                            "additionalProperties": Workflow.model_json_schema(),
                        },
                    },
                    "required": ["version", "name"],
                    "additionalProperties": False,
                },
                "step_result": StepResult.model_json_schema(),
                "lease": Lease.model_json_schema(),
            },
            indent=2,
        )
    )


@config_app.command("show")
def show_config(
    path: Annotated[Path, typer.Argument()] = Path("."),
    sources: bool = typer.Option(False, "--sources", help="Show imported YAML sources."),
):
    """Show the normalized project configuration."""
    try:
        package = snapshot(path)
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    result = {"configuration": package["configuration"]}
    if sources:
        result["config_source_version"] = package.get("config_source_version")
        result["config_sources"] = package.get("config_sources", {})
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@actions_app.command("list")
def list_actions(json_output: bool = typer.Option(False, "--json")):
    """List built-in action names and their required inputs."""
    definitions = {
        name: {
            **{key: value for key, value in descriptor.items() if key != "handler"},
            "required": descriptor["input_schema"].get("required", []),
            "optional": sorted(
                set(descriptor["input_schema"]["properties"])
                - set(descriptor["input_schema"].get("required", []))
            ),
        }
        for name, descriptor in sorted(ACTION_REGISTRY.items())
    }
    if json_output:
        typer.echo(json.dumps(definitions, indent=2, sort_keys=True))
        return
    for name, inputs in definitions.items():
        required = ", ".join(inputs["required"]) or "none"
        typer.echo(f"{name}: required inputs: {required}")


@actions_app.command("describe")
def describe_action(name: str, json_output: bool = typer.Option(False, "--json")):
    """Show one built-in action's input and result contracts."""
    if name not in ACTION_REGISTRY:
        raise typer.BadParameter(f"Unknown built-in action: {name}")
    descriptor = ACTION_REGISTRY[name]
    description = {
        "name": name,
        **{key: value for key, value in descriptor.items() if key != "handler"},
        "outputs_schema": descriptor["output_schema"],
        "result_schema": StepResult.model_json_schema(),
    }
    if json_output:
        typer.echo(json.dumps(description, indent=2, sort_keys=True))
        return
    typer.echo(f"{name}: {descriptor['description']}\nRequired inputs: {', '.join(descriptor['input_schema'].get('required', [])) or 'none'}")
    typer.echo(json.dumps(description, indent=2, sort_keys=True))


@app.command()
def enqueue(
    project: Annotated[Path, typer.Argument()],
    workflow: Annotated[str, typer.Argument()],
    database: Path = Path("engine.sqlite"),
):
    """Register the current project revision and queue a manual run."""
    from aorch.engine.storage import Store

    try:
        package = snapshot(project)
        store = Store(database)
        identity = str(project.resolve())
        store.register(identity, package)
        typer.echo(store.enqueue(identity, workflow))
    except (ConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@app.command()
def status(
    run_id: Annotated[str, typer.Argument()], database: Path = Path("engine.sqlite")
):
    """Show a durable run and its steps."""
    from aorch.engine.storage import Store

    try:
        typer.echo(json.dumps(Store(database).get_run(run_id), indent=2))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@app.command()
def engine(
    config: Annotated[Path, typer.Argument()],
    local_auth: Annotated[bool, typer.Option("--local-auth", help="Allow one-click sign-in on a loopback-only local deployment.")] = False,
    loopback_port_forward: Annotated[bool, typer.Option("--loopback-port-forward", help="Trust a proxy or Compose port that is bound to host loopback.")] = False,
    host: Annotated[str | None, typer.Option(help="Override the configured bind address.")] = None,
    port: Annotated[int | None, typer.Option(help="Override the configured port.")] = None,
):
    """Start one engine process and its scheduler."""
    import uvicorn

    from aorch.config.service import EngineConfig, load_service
    from aorch.engine.api import create_app

    settings = load_service(config, EngineConfig)
    if local_auth:
        settings.local_auth = True
    if loopback_port_forward:
        settings.loopback_port_forward = True
    if host is not None:
        settings.host = host
    if port is not None:
        settings.port = port
    if local_auth or loopback_port_forward or host is not None or port is not None:
        try:
            EngineConfig.model_validate(settings.model_dump())
        except Exception as error:
            raise typer.BadParameter(str(error), param_hint="--local-auth") from error
    from aorch.engine.operations import engine_lock

    with engine_lock(settings.database):
        uvicorn.run(
            create_app(settings), host=settings.host, port=settings.port, workers=1
        )


@app.command()
def runner(
    config: Annotated[Path, typer.Argument()],
    engine_url: Annotated[str | None, typer.Option(help="Override engine_url, for example http://engine:8765 inside Compose.")] = None,
    allow_insecure_local_network: Annotated[bool, typer.Option("--allow-insecure-local-network", help="Permit HTTP only for the private Compose service URL http://engine:8765.")] = False,
):
    """Start an authenticated outgoing runner."""
    runner_command(config, engine_url, allow_insecure_local_network)


def runner_command(config: Path, engine_url: str | None = None, allow_insecure_local_network: bool = False):
    import asyncio
    import signal

    from aorch.config.service import RunnerConfig, load_service
    from aorch.runner.client import Runner

    async def serve():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        settings = load_service(config, RunnerConfig)
        if engine_url:
            if engine_url == "http://engine:8765" and allow_insecure_local_network:
                settings.engine_url = engine_url
            else:
                settings.engine_url = engine_url
                RunnerConfig.model_validate(settings.model_dump())
        await Runner(settings).serve(stop)

    asyncio.run(serve())


@app.command("runner-enroll")
def runner_enroll(
    engine_url: Annotated[str, typer.Option(help="Public HTTPS engine URL, or a loopback URL for a local engine.")],
    setup_key: Annotated[str, typer.Option(help="One-time key from the engine's Add runner screen.")],
    runner_id: Annotated[str, typer.Option("--id")],
    directory: Annotated[Path, typer.Option(help="Private runner configuration directory.")] = Path("~/.config/runestra/runners"),
):
    """Enroll this machine as a runner and save its private credentials."""
    import os
    import re

    import httpx
    import yaml
    from pydantic import ValidationError

    from aorch.config.service import RunnerConfig

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", runner_id):
        raise typer.BadParameter("Invalid runner ID", param_hint="--id")
    try:
        RunnerConfig(id=runner_id, engine_url=engine_url.rstrip("/"), token_file=Path("runner.token"))
    except ValidationError as error:
        raise typer.BadParameter(f"Invalid engine URL: {error.errors()[0]['msg']}", param_hint="--engine-url") from error
    directory = directory.expanduser() / runner_id
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.parent.chmod(0o700)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    token_path = directory / "runner.token"
    config_path = directory / "runner.yaml"
    if config_path.exists():
        from aorch.config.service import RunnerConfig, load_service

        current = load_service(config_path, RunnerConfig)
        if current.id != runner_id or current.engine_url.rstrip("/") != engine_url.rstrip("/"):
            raise typer.BadParameter(f"A different runner is already configured at {directory}")
        if not token_path.exists():
            raise typer.BadParameter(f"Runner token is missing from {directory}")
        typer.echo(f"Runner {runner_id} is already enrolled. Its existing token was preserved.")
        return
    if token_path.exists():
        raise typer.BadParameter(f"An incomplete runner setup exists at {directory}")
    try:
        response = httpx.post(
            engine_url.rstrip("/") + "/runner/enroll",
            json={"setup_key": setup_key},
            timeout=15,
        )
        response.raise_for_status()
        enrolled = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise typer.BadParameter(f"Runner enrollment failed: {error}") from error
    if enrolled.get("runner_id") != runner_id or len(enrolled.get("token", "")) < 32:
        raise typer.BadParameter("Engine returned invalid runner credentials")
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(enrolled["token"] + "\n")
    config_path.write_text(yaml.safe_dump({
        "id": runner_id,
        "engine_url": engine_url.rstrip("/"),
        "token_file": "runner.token",
        "workspace_root": "workspaces",
        "journal": "runner.sqlite",
        "adapters": ["action", "shell", "codex", "claude"],
    }))
    config_path.chmod(0o600)
    typer.echo(f"Runner {runner_id} is enrolled. Start it with: uvx --from git+https://github.com/runestra-app/runestra.git aorch runner {config_path}")


@app.command("approve-runner")
def approve_runner(
    runner_id: Annotated[str, typer.Argument()],
    project: Annotated[list[str], typer.Option()],
    database: Path = Path("engine.sqlite"),
    token_file: Path = Path("runner.token"),
    capacity: int = 1,
    organization_id: str | None = typer.Option(None, "--organization-id"),
):
    """Approve or rotate a runner token and write a private token file."""
    import os

    from aorch.engine.storage import Store

    if token_file.exists():
        raise typer.BadParameter("Choose a new token file for rotation")
    token = Store(database).approve_runner(runner_id, project, capacity, organization_id)
    descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(token + "\n")
    typer.echo(f"Token written to {token_file}")


@app.command("bootstrap-owner")
def bootstrap_owner(
    email: Annotated[str, typer.Option(help="Email that must be verified with a sign-in link.")],
    username: Annotated[str, typer.Option()],
    organization: Annotated[str, typer.Option(help="Default organization slug.")],
    config: Annotated[Path | None, typer.Option(help="Engine YAML file. Its database path will be used.")] = None,
    database: Annotated[Path | None, typer.Option(help="Engine SQLite database path.")] = None,
):
    """Create the initial owner and assign existing projects to their organization."""
    from aorch.engine.storage import Store

    if (config is None) == (database is None):
        raise typer.BadParameter("Pass exactly one of --config or --database")
    if config is not None:
        from aorch.config.service import EngineConfig, load_service

        database = load_service(config, EngineConfig).database
    try:
        result = Store(database).bootstrap_owner(email, username, username, organization)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"Created pending owner {email} in organization {organization} ({result['organization_id']}). The owner must verify the email before signing in.")


@app.command("revoke-runner")
def revoke_runner(
    runner_id: Annotated[str, typer.Argument()], database: Path = Path("engine.sqlite")
):
    """Revoke a runner without discarding its uncertain assignments."""
    from aorch.engine.storage import Store

    Store(database).revoke_runner(runner_id)


@app.command("backup")
def backup_command(
    destination: Annotated[Path, typer.Argument()],
    config: Annotated[Path, typer.Option()],
):
    """Save a consistent database and artifact backup without printing secrets."""
    from aorch.config.service import EngineConfig, load_service
    from aorch.engine.operations import backup
    from aorch.engine.storage import Store

    settings = load_service(config, EngineConfig)
    backup(Store(settings.database), settings.artifacts, destination)
    typer.echo(f"Backup saved: {destination}")


@app.command("prune")
def prune_command(config: Annotated[Path, typer.Argument()], days: int = 30):
    """Prune logs and artifacts from old terminal runs, retaining result receipts."""
    import time

    from aorch.config.service import EngineConfig, load_service
    from aorch.engine.operations import prune_artifacts
    from aorch.engine.storage import Store

    if days < 1:
        raise typer.BadParameter("Retention must be at least one day")
    settings = load_service(config, EngineConfig)
    removed = prune_artifacts(
        Store(settings.database), settings.artifacts, time.time() - days * 86400
    )
    typer.echo(f"Pruned {removed} artifacts")


@app.command("init-local")
def init_local(
    project: Annotated[Path, typer.Argument()],
    directory: Path = Path(".aorch-local"),
    project_mount_path: str | None = typer.Option(None, "--project-mount-path", help="Path where Docker Compose mounts this project."),
):
    """Create private configuration for one local engine and runner."""
    import os
    import secrets

    import yaml

    from aorch.engine.storage import Store

    package = snapshot(project)
    name = package["configuration"]["name"]
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)

    def write_secret(filename, value):
        path = directory / filename
        if path.exists():
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(value + "\n")

    write_secret("session.key", secrets.token_urlsafe(32))
    store = Store(directory / "engine.sqlite")
    store.register(name, package)
    store.set_project(
        name,
        "local",
        project_mount_path or str(project.resolve()),
        path=project_mount_path or str(project.resolve()),
        status="ready",
    )
    if not (directory / "runner.token").exists():
        write_secret("runner.token", store.approve_runner("local", [name]))
    engine_config = directory / "engine.yaml"
    if not engine_config.exists():
        engine_config.write_text(
        yaml.safe_dump(
            {
                "database": "engine.sqlite",
                "artifacts": "artifacts",
                "session_key_file": "session.key",
                "public_base_url": "http://127.0.0.1:8765",
                "secrets_file": "auth-secrets.yaml",
                "registration_enabled": True,
            }
        ))
    write_secret("auth-secrets.yaml", "brevo_api_key: DUMMY_BREVO_API_KEY\ngoogle_client_secret: DUMMY_GOOGLE_CLIENT_SECRET\ngithub_client_secret: DUMMY_GITHUB_CLIENT_SECRET")
    runner_config = directory / "runner.yaml"
    if not runner_config.exists():
        runner_config.write_text(
        yaml.safe_dump(
            {
                "id": "local",
                "token_file": "runner.token",
                "workspace_root": "workspaces",
                "journal": "runner.sqlite",
                "adapters": ["action", "shell", "codex", "claude"],
            }
        ))
    typer.echo(f"Created {directory}.")
    typer.echo(f"1. Add your Brevo API key to {directory / 'auth-secrets.yaml'}.")
    typer.echo(
        "2. Create the owner with: "
        f"uv run aorch bootstrap-owner --email YOU@EXAMPLE.COM --username YOU "
        f"--organization YOUR-ORG --config {directory / 'engine.yaml'}"
    )
    typer.echo(f"3. Start the engine: uv run aorch engine {directory / 'engine.yaml'}")
    typer.echo(f"4. Start the runner: uv run aorch runner {directory / 'runner.yaml'}")
    typer.echo("5. Open http://127.0.0.1:8765 and request an email sign-in link.")
