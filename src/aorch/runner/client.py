"""Outgoing runner protocol with leases and durable result replay."""

import asyncio
from datetime import datetime
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aorch.config import snapshot
from aorch.config.service import GitProjectSource, read_secret
from aorch.contracts import PROTOCOL_VERSION
from aorch.extensions.schemas import SUPPORTED_ACTION_CONTRACTS
from aorch.runner.executor import (
    environment,
    execute,
    process_identity,
    project_environment_context,
)
from aorch.runner.git import git_environment
from aorch.runner.journal import Journal
from aorch.runner.log_store import LogStore
from aorch.runner.process import log_context


def redact_stream_chunk(previous, message, secrets_to_scrub):
    combined = previous + message
    redacted = combined
    for secret in secrets_to_scrub:
        redacted = redacted.replace(secret, "[REDACTED]")
    overlap = max((len(value) for value in secrets_to_scrub), default=1) - 1
    safe_length = max(0, len(redacted) - overlap)
    return redacted[:safe_length], redacted[safe_length:]


class Runner:
    def __init__(self, config):
        self.config = config
        runner_token = read_secret(config.token_file)
        self.journal = Journal(config.journal, secrets_key=runner_token)
        self.logs = LogStore(config.log_directory, config.log_retention_days, config.log_max_total_bytes, config.log_max_attempt_bytes)
        self.instance_id = str(uuid.uuid4())
        self.client = httpx.AsyncClient(
            base_url=config.engine_url,
            headers={"Authorization": f"Bearer {runner_token}"},
            timeout=10,
        )
        self.workspace_locks = {}
        self.registration = {
            "runner_id": config.id,
            "instance_id": self.instance_id,
            "protocol_version": PROTOCOL_VERSION,
            "capacity": config.capacity,
            "adapters": config.adapters,
            "resources": config.resources,
            "action_contracts": SUPPORTED_ACTION_CONTRACTS,
            "log_retention": True,
        }

    def console(self, message):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"[{timestamp}] runner[{self.config.id}] {message}", flush=True)

    async def post(self, path, body=None, timeout=None):
        response = await self.client.post(path, json=body or {}, timeout=timeout)
        response.raise_for_status()
        return response.json()

    async def upload_artifacts(self, assignment, result):
        root = (
            self.config.workspace_root
            / assignment.get("workspace_key", assignment["run_id"])
        ).resolve()
        import hashlib

        for artifact in result.get("artifacts", []):
            path = (root / artifact["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Artifact escapes workspace")
            data = path.read_bytes()
            if (
                len(data) != artifact["size"]
                or hashlib.sha256(data).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("Artifact changed after execution")
            response = await self.client.post(
                "/runner/artifact",
                params={
                    "lease_id": assignment["lease_id"],
                    "generation": assignment["generation"],
                },
                content=data,
            )
            response.raise_for_status()
            artifact["path"] = response.json()["path"]
        return result

    async def serve_log_request(self):
        try:
            request = await self.client.get("/runner/log-request", timeout=6)
            request.raise_for_status()
            item = request.json()
            if item is None:
                return
            if item.get("kind") == "project-refresh":
                await self.serve_project_refresh(item)
                return
            if item.get("kind") == "policy":
                if item["deleted"]:
                    self.logs.delete(item["attempt_id"])
                else:
                    self.logs.set_retained(item["attempt_id"], item["retained"])
                await self.post("/runner/log-response", {
                    "attempt_id": item["attempt_id"],
                    "policy_generation": item["generation"],
                })
                return
            if item.get("kind") == "delete":
                self.logs.delete(item["attempt_id"])
                return
            result = self.logs.read(item["attempt_id"], item["after"], item["limit"], item["view"])
            await self.post("/runner/log-response", {"request_id": item["request_id"], "result": result})
        except (httpx.TransportError, httpx.HTTPStatusError, OSError, ValueError):
            return

    async def serve_project_refresh(self, item):
        self.console(f"Refreshing project {item['project']}")
        token = project_environment_context.set(item.get("project_environment", {}))
        try:
            response = await asyncio.to_thread(self.snapshot_project, item)
            self.console(
                f"Project {item['project']} refreshed at {response['commit'][:12]}"
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            code = error.returncode if isinstance(error, subprocess.CalledProcessError) else None
            self.console(
                f"Project refresh failed for {item['project']}: "
                f"{type(error).__name__}"
                f"{f' (exit code {code})' if code is not None else ''}"
            )
            response = {
                "request_id": item["request_id"],
                "error": f"Runner Git refresh failed{f' with exit code {code}' if code is not None else ''}",
            }
        finally:
            project_environment_context.reset(token)
        try:
            await self.post("/runner/project-refresh-response", response, timeout=60)
        except (httpx.TransportError, httpx.HTTPStatusError):
            return

    def snapshot_project(self, item):
        source = GitProjectSource.model_validate({"git": item["git"], "ref": item["ref"]})
        remote = source.git
        parsed = urlsplit(remote if not remote.startswith("git@") else f"ssh://{remote[4:].replace(':', '/', 1)}")
        if parsed.scheme not in ("https", "ssh") or not parsed.hostname:
            raise ValueError("Unsupported Git transport")
        env = environment(self.config, authentication="server")
        with tempfile.TemporaryDirectory(prefix="runestra-refresh-") as temporary:
            root = Path(temporary)
            git_env = git_environment(
                env, remote, root,
                project_names=item.get("project_environment", {}).keys(),
            )
            checkout = root / "checkout"
            def git(arguments, timeout=180):
                return subprocess.run(
                    arguments, check=True, capture_output=True, text=True, timeout=timeout, env=git_env
                )
            git(["git", "-c", "http.followRedirects=false", "clone", "--no-checkout", remote, str(checkout)])
            git(["git", "-c", "http.followRedirects=false", "-C", str(checkout), "fetch", "origin", source.ref])
            commit = git(["git", "-C", str(checkout), "rev-parse", "FETCH_HEAD"], timeout=30).stdout.strip()
            if not commit or any(character not in "0123456789abcdef" for character in commit):
                raise ValueError("Git returned an invalid commit")
            git(["git", "-C", str(checkout), "checkout", "--force", commit], timeout=60)
            package = snapshot(checkout)
        return {"request_id": item["request_id"], "commit": commit, "package": package}

    async def recover_resources(self, attempt):
        from aorch.runner.executor import environment
        from aorch.runner.process import run_process

        for resource in self.journal.active_resources(attempt):
            if resource["platform"] == "ios":
                code, output, _ = await run_process(
                    ["xcrun", "simctl", "list", "devices", "--json"],
                    self.config.workspace_root,
                    environment(self.config),
                    "",
                    30,
                    asyncio.Event(),
                )
                if code:
                    return False
                match = next(
                    (
                        entry
                        for entries in json.loads(output)["devices"].values()
                        for entry in entries
                        if entry["udid"] == resource["identifier"]
                    ),
                    None,
                )
                if match is None:
                    return False
                if match["state"] != "Shutdown":
                    code, _, _ = await run_process(
                        ["xcrun", "simctl", "shutdown", resource["identifier"]],
                        self.config.workspace_root,
                        environment(self.config),
                        "",
                        30,
                        asyncio.Event(),
                    )
                    if code:
                        return False
            elif resource["pid"]:
                if process_identity(resource["pid"]) == resource["process_identity"]:
                    try:
                        os.killpg(resource["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.sleep(0.1)
                try:
                    os.killpg(resource["pid"], 0)
                except ProcessLookupError:
                    pass
                else:
                    return False
            else:
                return False
            self.journal.resource_stopped(attempt, resource["name"])
        return True

    async def recover(self):
        for entry in self.journal.pending():
            assignment = json.loads(entry["assignment"])
            identity = {
                "lease_id": assignment["lease_id"],
                "generation": assignment["generation"],
            }
            if not await self.recover_resources(entry["attempt"]):
                await self.post(
                    "/runner/stopped", {**identity, "uncertain": True, "stopped": False}
                )
                continue
            if entry["state"] == "completed":
                try:
                    await self.post(
                        "/runner/result",
                        {
                            **identity,
                            "result": await self.upload_artifacts(
                                assignment, json.loads(entry["result"])
                            ),
                        },
                    )
                    self.journal.delivered(entry["attempt"])
                    continue
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 409:
                        raise
            elif entry["pid"]:  # noqa: SIM102
                # A journaled PID is insufficient: check creation time before signaling.
                if process_identity(entry["pid"]) == entry["process_identity"]:
                    try:
                        os.killpg(entry["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.sleep(0.1)
                    if process_identity(entry["pid"]) == entry["process_identity"]:
                        continue
            if entry["state"] == "accepted" and not entry["pid"]:
                await self.post(
                    "/runner/stopped", {**identity, "uncertain": True, "stopped": False}
                )
                continue
            if entry["pid"]:
                try:
                    os.killpg(entry["pid"], 0)
                except ProcessLookupError:
                    pass
                else:
                    # Do not release a workspace when descendants might still be active.
                    continue
            self.logs.append(entry["attempt"], {"kind": "recovery", "source": "runner", "message": "Runner recovered the interrupted attempt and confirmed the process stopped."})
            self.logs.complete(entry["attempt"])
            await self.post("/runner/stopped", {**identity, "uncertain": True})
            self.journal.delivered(entry["attempt"])

    async def perform(self, assignment):
        self.journal.save(assignment)
        try:
            self.logs.prune()
        except OSError:
            pass
        attempt_id = assignment["attempt_id"]
        run_id = assignment["run_id"]
        step_name = assignment["step_name"]
        self.console(
            f"Started run {run_id}, workflow {assignment.get('workflow_name', 'unknown')}, "
            f"step {step_name}"
        )
        package = assignment.get("package", {})
        secrets_to_scrub = self.secret_values(package, assignment)
        stream_tails = {"stdout": "", "stderr": ""}
        log_failure = False
        def append_log(event):
            nonlocal log_failure
            try:
                self.logs.append(attempt_id, event)
            except OSError:
                log_failure = True
        def log_event(kind, data):
            if kind == "stream":
                stream = data["stream"]
                ready, stream_tails[stream] = redact_stream_chunk(
                    stream_tails[stream], data.get("message", ""), secrets_to_scrub
                )
                if ready:
                    append_log({"kind": kind, "source": "runner", "stream": stream, "message": ready})
                return
            append_log({"kind": kind, "source": "runner", **self.scrub_log(data, package, assignment)})
        log_event("step.started", {"message": f"Started {assignment['step_name']}"})
        log_token = log_context.set(log_event)
        identity = {
            "lease_id": assignment["lease_id"],
            "generation": assignment["generation"],
        }
        cancelled = asyncio.Event()
        lease_lost = False

        async def renew():
            nonlocal lease_lost
            deadline = time.monotonic() + 25
            while not cancelled.is_set():
                await asyncio.sleep(5)
                try:
                    await self.post("/runner/renew", identity)
                    deadline = time.monotonic() + 25
                except httpx.HTTPStatusError:
                    lease_lost = True
                    cancelled.set()
                except httpx.TransportError:
                    if time.monotonic() >= deadline:
                        lease_lost = True
                        cancelled.set()

        renewal = asyncio.create_task(renew())
        try:
            lock = self.workspace_locks.setdefault(
                assignment.get("workspace_key", assignment["run_id"]), asyncio.Lock()
            )
            async with lock:
                if cancelled.is_set():
                    from aorch.contracts import StepResult

                    result = StepResult(
                        status="cancelled", summary="Stopped before workspace execution"
                    )
                else:
                    result = await execute(
                        assignment, self.config, self.journal, cancelled
                    )
        except Exception as error:  # noqa: BLE001
            from aorch.contracts import StepResult

            result = StepResult(
                status="failed", summary=str(error), error=type(error).__name__
            )
        finally:
            cancelled.set()
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
        result = self.redact(result, package, assignment)
        if assignment["step"]["type"] == "agent":
            for key in ("events", "agent_events", "stderr"):
                result.outputs.pop(key, None)
        workflow = assignment.get("package", {}).get("configuration", {}).get("workflows", {}).get(assignment.get("workflow_name"), {})
        refs_stdout = any(
            reference.startswith(
                (
                    f"steps.{assignment['step_name']}.outputs.stdout",
                    f"steps.{assignment['step_name']}.outputs.stderr",
                )
            )
            for definition in workflow.get("steps", {}).values()
            for reference in definition.get("input_refs", {}).values()
        )
        if not refs_stdout:
            result.outputs.pop("stdout", None)
            result.outputs.pop("stderr", None)
        for stream, tail in stream_tails.items():
            for secret in secrets_to_scrub:
                tail = tail.replace(secret, "[REDACTED]")
            if tail:
                append_log({"kind": "stream", "source": "runner", "stream": stream, "message": tail})
        log_event("step.completed", {"message": result.summary, "status": result.status})
        safe_summary = result.summary.replace("\n", " ").strip()
        if len(safe_summary) > 400:
            safe_summary = safe_summary[:397] + "..."
        self.console(f"Finished run {run_id}, step {step_name}: {result.status}. {safe_summary}")
        try:
            result.log_manifest = self.logs.complete(attempt_id) if not log_failure else None
        except OSError:
            result.log_manifest = None
        log_context.reset(log_token)
        self.journal.completed(assignment["attempt_id"], result.model_dump(mode="json"))
        if lease_lost:
            await self.post("/runner/stopped", {**identity, "uncertain": True})
            self.journal.delivered(assignment["attempt_id"])
            return
        # Persist first; reconnect can replay after any interruption here.
        try:
            await self.post(
                "/runner/events",
                {**identity, "sequence": 1, "payload": {"summary": result.summary}},
            )
            await self.post(
                "/runner/result",
                {
                    **identity,
                    "result": await self.upload_artifacts(
                        assignment, result.model_dump(mode="json")
                    ),
                },
            )
            self.journal.delivered(assignment["attempt_id"])
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 409:
                raise
            await self.post("/runner/stopped", {**identity, "uncertain": True})
            self.journal.delivered(assignment["attempt_id"])

    def secret_values(self, package=None, assignment=None):
        names = {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "CODEX_API_KEY",
            *self.config.redact_env,
            *self.config.api_credentials.values(),
        }
        names.update(
            connection["token_env"]
            for connection in self.config.connections.values()
            if "token_env" in connection
        )
        configuration = (package or {}).get("configuration", {})
        names.update(
            profile["credential_env"]
            for profile in configuration.get("profiles", {}).values()
            if profile.get("credential_env")
        )
        values = {os.environ[name] for name in names if os.environ.get(name)}
        assignment = assignment or {}
        project_environment = assignment.get("project_environment", {})
        values.update(
            project_environment[name]
            for name in assignment.get("project_secret_names", [])
            if project_environment.get(name)
        )
        try:
            values.add(read_secret(self.config.token_file))
        except (OSError, ValueError):
            pass
        return sorted(values, key=len, reverse=True)

    @staticmethod
    def scrub_text(value, secrets_to_scrub):
        if isinstance(value, str):
            for secret in secrets_to_scrub:
                value = value.replace(secret, "[REDACTED]")
        return value

    def redact(self, result, package=None, assignment=None):
        from aorch.contracts import StepResult

        values = self.secret_values(package, assignment)

        def scrub(value):
            if isinstance(value, str):
                return self.scrub_text(value, values)
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        return StepResult.model_validate(scrub(result.model_dump(mode="json")))

    def scrub_log(self, data, package=None, assignment=None):
        secrets_to_scrub = self.secret_values(package, assignment)
        def clean(value):
            if isinstance(value, str):
                return self.scrub_text(value, secrets_to_scrub)
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value
        return clean(data)

    async def serve(self, stop: asyncio.Event):
        active = set()
        capacity = self.config.capacity
        engine = urlsplit(self.config.engine_url)
        endpoint = f"{engine.scheme}://{engine.netloc}"
        self.console(
            f"Starting, connecting to {endpoint}, protocol {PROTOCOL_VERSION}, "
            f"capacity {capacity}"
        )
        connected = False
        last_connection_error = None
        async def poll_logs():
            while not stop.is_set():
                await self.serve_log_request()
        log_task = asyncio.create_task(poll_logs())
        try:
            while not stop.is_set():
                try:
                    health = await self.client.get("/health")
                    health.raise_for_status()
                    if health.json().get("protocol_version") != PROTOCOL_VERSION:
                        raise RuntimeError(
                            "Engine and runner protocol versions differ; upgrade the engine first"
                        )
                    registration = await self.post(
                        "/runner/register", self.registration
                    )
                    if registration["protocol_version"] != PROTOCOL_VERSION:
                        raise RuntimeError(
                            "Engine and runner protocol versions differ; upgrade the engine first"
                        )
                    capacity = registration["capacity"]
                    if not connected:
                        self.console(
                            f"Connected and registered with engine, protocol "
                            f"{registration['protocol_version']}, capacity {capacity}"
                        )
                    connected = True
                    last_connection_error = None
                    if not active:
                        await self.recover()
                    while len(active) < capacity and not stop.is_set():
                        assignment = await self.post("/runner/claim")
                        if assignment is None:
                            break
                        self.console(
                            f"Claimed run {assignment['run_id']}, "
                            f"workflow {assignment.get('workflow_name', 'unknown')}, "
                            f"step {assignment['step_name']}"
                        )
                        task = asyncio.create_task(self.perform(assignment))
                        active.add(task)
                    done = {task for task in active if task.done()}
                    for task in done:
                        task.result()
                    active -= done
                    await asyncio.sleep(0.5)
                except httpx.HTTPStatusError as error:
                    request_path = error.request.url.path
                    if request_path == "/runner/register" and error.response.status_code in (409, 422):
                        try:
                            detail = error.response.json().get("detail")
                        except ValueError:
                            detail = None
                        self.console(
                            detail or "Engine rejected registration because this runner ID is already active."
                        )
                        raise SystemExit(1)
                    message = (
                        f"Engine returned HTTP {error.response.status_code} for "
                        f"{request_path}; retrying"
                    )
                    if connected or message != last_connection_error:
                        self.console(message)
                    connected = False
                    last_connection_error = message
                    await asyncio.sleep(1)
                except httpx.TransportError as error:
                    safe_error = self.scrub_text(str(error), self.secret_values())
                    message = (
                        f"Cannot reach engine ({type(error).__name__}: {safe_error}); "
                        "retrying"
                    )
                    if connected or message != last_connection_error:
                        self.console(message)
                    connected = False
                    last_connection_error = message
                    await asyncio.sleep(1)
                except RuntimeError as error:
                    self.console(f"Fatal runner error: {error}")
                    raise
                    await asyncio.sleep(1)
        finally:
            log_task.cancel()
            await asyncio.gather(log_task, return_exceptions=True)
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            await self.client.aclose()
