"""Authenticated runner protocol and browser administration."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jsonschema.exceptions import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from aorch.config import snapshot
from aorch.config.service import EngineConfig, GitProjectSource, read_secret
from aorch.contracts import (
    PROTOCOL_VERSION,
    AssignmentRequest,
    Completion,
    Registration,
    RunnerEvent,
    StopConfirmation,
)
from aorch.engine.errors import StateConflict
from aorch.engine.scheduler import Scheduler
from aorch.engine.storage import Store


def _rehash_package(package):
    payload = {key: value for key, value in package.items() if key != "sha256"}
    package["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_notification_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Notification URL must contain 1 to 2048 characters")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Notification URL must be a credential-free HTTPS URL")
    try:
        addresses = {ipaddress.ip_address(parsed.hostname)}
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
            raise ValueError("Notification URL host must be publicly routable")
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as error:
            raise ValueError("Notification URL host could not be resolved") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Notification URL host must be publicly routable")
    return value


async def bounded_body(request, maximum):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise HTTPException(413, "Upload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(config: EngineConfig):
    config.database.parent.mkdir(parents=True, exist_ok=True)
    config.artifacts.mkdir(parents=True, exist_ok=True)
    session_secret = read_secret(config.session_key_file)
    store = Store(config.database, config.artifacts, secrets_key=session_secret)
    reload_errors = {}
    log_queues = {}
    log_responses = {}
    refresh_responses = {}
    project_refresh_locks = {}
    project_refresh_tasks = {}
    webhook_requests = {}
    source_config = {}
    for row in store.overview()["projects"]:
        if row["source_type"] == "git":
            source_config[row["name"]] = GitProjectSource(
                git=row["source"], ref=row["ref"] or "HEAD"
            )
        elif row["source_type"] == "local":
            source_config[row["name"]] = Path(row["source"])

    def validate_git_host(source):
        parsed = urlsplit(source.git if not source.git.startswith("git@") else f"ssh://{source.git[4:].replace(':', '/', 1)}")
        host = parsed.hostname
        if parsed.scheme not in ("https", "ssh") or host not in config.git_allowed_hosts:
            raise ValueError(f"Git source must use HTTPS or SSH on an allowed host: {host or '<missing>'}")
        if parsed.scheme == "https" and parsed.port not in (None, 443):
            raise ValueError("HTTPS Git sources must use port 443")
        if parsed.scheme == "ssh" and parsed.port not in (None, 22):
            raise ValueError("SSH Git sources must use port 22")

    async def _refresh_project(name):
        source = source_config[name]
        if isinstance(source, GitProjectSource):
            store.project_status(name, "refreshing", None)
            try:
                validate_git_host(source)
                project_identity = next(
                    (row for row in store.overview()["projects"] if row["name"] == name), None
                )
                if project_identity is None:
                    raise ValueError("Unknown project")
                with store.read_transaction() as db:
                    organization = db.execute(
                        "SELECT organization_id FROM project_organizations WHERE project=?", (name,)
                    ).fetchone()
                if organization is None:
                    raise ValueError("Project organization is missing")
                runners = [
                    row for row in store.overview()["runners"]
                    if row["online"]
                    and row["organization_id"] == organization["organization_id"]
                    and name in json.loads(row["projects"])
                    and json.loads(row["capabilities"]).get("protocol_version")
                    == PROTOCOL_VERSION
                ]
                if not runners:
                    raise ValueError(
                        f"No online protocol {PROTOCOL_VERSION} runner is assigned to this project"
                    )
                runner_id = runners[0]["id"]
                request_id = secrets.token_urlsafe(24)
                future = asyncio.get_running_loop().create_future()
                refresh_responses[request_id] = {"runner": runner_id, "future": future}
                queue = log_queues.setdefault(runner_id, asyncio.Queue(maxsize=16))
                environment = store.list_project_environment(name, include_values=True)
                queue.put_nowait({
                    "kind": "project-refresh",
                    "request_id": request_id,
                    "project": name,
                    "git": source.git,
                    "ref": source.ref,
                    "project_environment": {item["name"]: item["value"] for item in environment},
                    "project_secret_names": [item["name"] for item in environment if item["secret"]],
                })
                try:
                    response = await asyncio.wait_for(future, timeout=480)
                finally:
                    refresh_responses.pop(request_id, None)
                if response.get("error"):
                    raise ValueError(response["error"])
                package = response.get("package")
                commit = response.get("commit")
                if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                    raise ValueError("Runner returned an invalid Git commit")
                if not isinstance(package, dict):
                    raise TypeError("Runner returned an invalid project snapshot")
                from aorch.runner.package import verify_package

                verify_package(package)
                package["repository"] = {"git": source.git, "ref": source.ref, "commit": commit}
                _rehash_package(package)
                store.register(name, package)
                store.set_project(name, "git", source.git, source.ref, None, commit, "ready", None, time.time())
                old_path = project_identity.get("path")
                if old_path:
                    cache_root = (config.database.parent / ".aorch-projects").resolve()
                    checkout = Path(old_path).resolve()
                    if checkout != cache_root and checkout.is_relative_to(cache_root):
                        shutil.rmtree(checkout, ignore_errors=True)
                reload_errors.pop(name, None)
                return {"status": "ready", "commit": commit}
            except (OSError, ValueError, TypeError, AttributeError, asyncio.QueueFull, TimeoutError) as error:
                message = str(error) or "Runner project refresh timed out"
                if "missing" in message.lower() or "no such file" in message.lower():
                    status = "missing-config"
                else:
                    status = "error"
                store.set_project(name, "git", source.git, source.ref, None, None, status, message, None)
                reload_errors[name] = message
                return {"status": status, "error": message}
            except (ValidationError, KeyError, IndexError):
                message = "Runner project refresh failed while validating its response"
                store.set_project(name, "git", source.git, source.ref, None, None, "error", message, None)
                reload_errors[name] = message
                return {"status": "error", "error": message}
        return await asyncio.to_thread(refresh_local_project, name)

    async def refresh_project(name, force=False):
        current = project_refresh_tasks.get(name)
        if current and not current.done():
            if not force:
                return await asyncio.shield(current)
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)

        lock = project_refresh_locks.setdefault(name, asyncio.Lock())

        async def run_refresh():
            async with lock:
                return await _refresh_project(name)

        task = asyncio.create_task(run_refresh())
        project_refresh_tasks[name] = task
        try:
            return await asyncio.shield(task)
        finally:
            if project_refresh_tasks.get(name) is task:
                project_refresh_tasks.pop(name, None)

    def refresh_local_project(name):
        source = source_config[name]
        store.project_status(name, "refreshing", None)
        try:
            package = snapshot(source)
            git_dir = source / ".git"
            commit = None
            if git_dir.exists():
                commit = subprocess.run(
                    ["git", "-C", str(source), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True, timeout=15,
                ).stdout.strip()
                remote = subprocess.run(
                    ["git", "-C", str(source), "remote", "get-url", "origin"],
                    check=False, capture_output=True, text=True, timeout=15,
                ).stdout.strip()
                if remote:
                    ref = subprocess.run(
                        ["git", "-C", str(source), "symbolic-ref", "--quiet", "--short", "HEAD"],
                        check=False, capture_output=True, text=True, timeout=15,
                    ).stdout.strip() or "HEAD"
                    package["repository"] = {"git": remote, "ref": ref, "commit": commit}
                    _rehash_package(package)
            store.register(name, package)
            store.set_project(name, "local", str(source), None, str(source), commit, "ready", None, time.time())
            reload_errors.pop(name, None)
            return {"status": "ready", "commit": commit}
        except ValueError as error:
            store.set_project(name, "local", str(source), None, str(source), None, "missing-config", str(error))
            reload_errors[name] = str(error)
            return {"status": "missing-config", "error": str(error)}

    async def reload_projects():
        for name, source in source_config.items():
            if not isinstance(source, GitProjectSource):
                await refresh_project(name)

    @asynccontextmanager
    async def lifespan(app):
        await reload_projects()
        stop = asyncio.Event()
        scheduler = asyncio.create_task(
            Scheduler(store, refresh_project, public_base_url=config.public_base_url).serve(stop)
        )
        yield
        stop.set()
        await scheduler

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("web")), name="static")
    app.state.store = store
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        https_only=config.secure_cookies,
        same_site="lax",
        max_age=config.session_ttl_seconds,
    )
    oauth = OAuth()
    google_enabled = bool(config.google_client_id and config.google_client_secret and not config.google_client_id.startswith("DUMMY_") and not config.google_client_secret.startswith("DUMMY_"))
    github_enabled = bool(config.github_client_id and config.github_client_secret and not config.github_client_id.startswith("DUMMY_") and not config.github_client_secret.startswith("DUMMY_"))
    if google_enabled:
        oauth.register(
            name="google", client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )
    if github_enabled:
        oauth.register(
            name="github", client_id=config.github_client_id,
            client_secret=config.github_client_secret,
            authorize_url="https://github.com/login/oauth/authorize",
            access_token_url="https://github.com/login/oauth/access_token",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "read:user user:email", "code_challenge_method": "S256"},
        )

    @app.exception_handler(StateConflict)
    async def conflict_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValidationError)
    async def schema_handler(request, error):
        return JSONResponse(
            {"detail": "Result does not match declared schema"}, status_code=422
        )

    @app.middleware("http")
    async def body_limit(request, call_next):
        if request.method in ("POST", "PUT") and not request.url.path.endswith(
            "/artifact"
        ):
            request._body = await bounded_body(request, 1024 * 1024)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def runner(request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "Runner token required")
        try:
            return store.authenticate(header[7:])
        except StateConflict as error:
            raise HTTPException(401, "Invalid runner token") from error

    def admin(request: Request):
        user_id = request.session.get("user_id")
        if user_id:
            session_hash = request.session.get("session_hash")
            try:
                user = store.authenticate_browser_session(session_hash)
            except (StateConflict, TypeError):
                request.session.clear()
                raise HTTPException(401, "Browser login required")
            organization_id = request.session.get("organization_id")
            with store.transaction() as db:
                membership = db.execute(
                    "SELECT role FROM organization_memberships WHERE user_id=? AND organization_id=?",
                    (user["id"], organization_id),
                ).fetchone()
            if membership is None:
                raise HTTPException(403, "Organization membership required")
            request.state.user = user
            request.state.organization_id = organization_id
            request.state.role = membership["role"]
        else:
            request.session.clear()
            raise HTTPException(401, "Browser login required")
        if request.method != "GET" and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""),
            request.session.get("csrf", "missing"),
        ):
            raise HTTPException(403, "CSRF token required")

    def require_project_access(request: Request, project: str):
        organization_id = getattr(request.state, "organization_id", None)
        if organization_id is None:
            raise HTTPException(401, "Browser login required")
        with store.transaction() as db:
            row = db.execute("SELECT organization_id FROM project_organizations WHERE project=?", (project,)).fetchone()
        if row is None or row["organization_id"] != organization_id:
            raise HTTPException(404, "Project not found")

    def require_project_admin(request: Request, project: str):
        require_project_access(request, project)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Project environment changes are restricted")

    @app.get("/admin/projects/{project}/environment", dependencies=[Depends(admin)])
    def project_environment(project: str, request: Request):
        require_project_access(request, project)
        return {"variables": store.list_project_environment(project)}

    @app.put("/admin/projects/{project}/environment", dependencies=[Depends(admin)])
    async def set_project_environment(project: str, request: Request):
        require_project_admin(request, project)
        body = await request.json()
        name, value = body.get("name"), body.get("value")
        secret = body.get("secret", False)
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
            or name.startswith("AORCH_")
            or name in {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "SHELL"}
        ):
            raise HTTPException(422, "Name must be a valid environment variable name")
        if not isinstance(secret, bool):
            raise HTTPException(422, "Secret must be a boolean")
        try:
            store.set_project_environment(project, name, value, secret)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        with store.transaction() as db:
            db.execute(
                "INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)",
                (request.state.user["id"], request.state.organization_id, "project.environment.set", f"{project}:{name}", time.time()),
            )
        return {"name": name, "secret": secret, "configured": True}

    @app.delete("/admin/projects/{project}/environment/{name}", dependencies=[Depends(admin)])
    def delete_project_environment(project: str, name: str, request: Request):
        require_project_admin(request, project)
        if not store.delete_project_environment(project, name):
            raise HTTPException(404, "Environment variable not found")
        with store.transaction() as db:
            db.execute(
                "INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)",
                (request.state.user["id"], request.state.organization_id, "project.environment.deleted", f"{project}:{name}", time.time()),
            )
        return {"deleted": True}

    def require_run_access(request: Request, run: str):
        with store.transaction() as db:
            row = db.execute("SELECT project FROM runs WHERE id=?", (run,)).fetchone()
        if row is None:
            raise HTTPException(404, "Run not found")
        require_project_access(request, row["project"])

    @app.get("/health")
    def health():
        return {"status": "ok", "protocol_version": PROTOCOL_VERSION}

    @app.post("/hooks/{hook_id}")
    async def receive_webhook(hook_id: str, request: Request):
        hook = config.webhook_triggers.get(hook_id)
        if hook is None or not isinstance(hook, dict):
            raise HTTPException(404, "Webhook not found")
        remote = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [stamp for stamp in webhook_requests.get(remote, []) if now - stamp < 60]
        if len(attempts) >= 60:
            raise HTTPException(429, "Webhook rate limit exceeded")
        attempts.append(now)
        webhook_requests[remote] = attempts
        if len(webhook_requests) > 10000:
            webhook_requests.clear()
        provider = hook.get("provider")
        secret = hook.get("secret")
        if provider not in {"github", "hmac"} or not isinstance(secret, str) or len(secret) < 32:
            raise HTTPException(503, "Webhook configuration is incomplete")
        raw = await bounded_body(request, 262144)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(400, "Webhook body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise HTTPException(400, "Webhook body must be a JSON object")
        if provider == "github":
            expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            supplied = request.headers.get("x-hub-signature-256", "")
            delivery = request.headers.get("x-github-delivery", "")
            event = request.headers.get("x-github-event", "")
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(401, "Invalid webhook signature")
            if not delivery or len(delivery) > 200:
                raise HTTPException(400, "Missing or invalid delivery identifier")
            if event != hook.get("event", "issues"):
                return {"accepted": False, "reason": "event_not_selected"}
            allowed_actions = hook.get("actions", ["opened", "reopened", "edited"])
            if payload.get("action") not in allowed_actions:
                return {"accepted": False, "reason": "action_not_selected"}
            repository_data = payload.get("repository")
            repository = repository_data.get("full_name") if isinstance(repository_data, dict) else None
            if repository != hook.get("repository"):
                raise HTTPException(403, "Repository is not allowed")
        else:
            timestamp = request.headers.get("x-runestra-timestamp", "")
            delivery = request.headers.get("x-runestra-event-id", "")
            supplied = request.headers.get("x-runestra-signature", "")
            try:
                sent_at = int(timestamp)
            except ValueError as error:
                raise HTTPException(401, "Invalid webhook timestamp") from error
            if abs(time.time() - sent_at) > 300:
                raise HTTPException(401, "Webhook timestamp is outside the allowed window")
            expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(401, "Invalid webhook signature")
            if not delivery or len(delivery) > 200:
                raise HTTPException(400, "Missing or invalid event identifier")
        project, workflow = hook.get("project"), hook.get("workflow")
        mapping = hook.get("input_mapping")
        if not isinstance(project, str) or not isinstance(workflow, str) or not isinstance(mapping, dict) or not mapping:
            raise HTTPException(503, "Webhook target configuration is invalid")
        inputs = {}
        for target, reference in mapping.items():
            if not isinstance(target, str) or not target.isidentifier() or not isinstance(reference, str):
                raise HTTPException(503, "Webhook input mapping is invalid")
            value = payload
            for part in reference.split("."):
                if not part or part.startswith("_") or not isinstance(value, dict) or part not in value:
                    raise HTTPException(422, "Webhook payload is missing a mapped field")
                value = value[part]
            inputs[target] = value
        digest = hashlib.sha256(raw).hexdigest()
        try:
            run, created = store.enqueue_webhook(hook_id, delivery, digest, project, workflow, inputs)
        except (ValueError, StateConflict) as error:
            raise HTTPException(422, str(error)) from error
        return {"accepted": True, "created": created, "run_id": run}

    @app.post("/admin/runners", dependencies=[Depends(admin)])
    def create_runner_setup(request: Request, body: dict):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can add runners")
        runner_name = body.get("name", "")
        runner_name = runner_name.strip() if isinstance(runner_name, str) else ""
        projects = body.get("projects", [])
        capacity = body.get("capacity", 1)
        if not runner_name or len(runner_name) > 64 or not runner_name.isprintable():
            raise HTTPException(422, "Runner name must be 1 to 64 printable characters")
        if (
            not isinstance(projects, list)
            or not projects
            or any(not isinstance(project, str) for project in projects)
            or len(projects) != len(set(projects))
        ):
            raise HTTPException(422, "Choose at least one project without duplicates")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or not 1 <= capacity <= 64:
            raise HTTPException(422, "Runner capacity must be between 1 and 64")
        for project in projects:
            require_project_access(request, project)
        runner_id, key = store.create_runner_enrollment(
            runner_name, projects, capacity, request.state.organization_id,
        )
        command = (
            'bash -c "$(curl -fsSL https://raw.githubusercontent.com/runestra-app/runestra/main/scripts/install-runner.sh)" -- '
            f"--engine-url {shlex.quote(config.public_base_url)} "
            f"--setup-key {shlex.quote(key)} --id {shlex.quote(runner_id)}"
        )
        return {"id": runner_id, "name": runner_name, "command": command, "expires_in": 3600}

    @app.delete("/admin/runners/{runner_id}", dependencies=[Depends(admin)])
    def revoke_runner_admin(runner_id: str, request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can revoke runners")
        with store.read_transaction() as db:
            identity = db.execute(
                "SELECT organization_id FROM runners WHERE id=?", (runner_id,),
            ).fetchone()
        if identity is None or identity["organization_id"] != request.state.organization_id:
            raise HTTPException(404, "Runner not found")
        store.revoke_runner(runner_id)
        return {"revoked": runner_id}

    @app.put("/admin/runners/{runner_id}/projects", dependencies=[Depends(admin)])
    async def update_runner_projects(runner_id: str, request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can change runner access")
        body = await request.json()
        projects = body.get("projects")
        if (
            not isinstance(projects, list)
            or any(not isinstance(project, str) for project in projects)
            or len(projects) != len(set(projects))
        ):
            raise HTTPException(422, "Choose one or more projects without duplicates")
        try:
            store.set_runner_projects(runner_id, projects, request.state.organization_id)
        except StateConflict as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"runner_id": runner_id, "projects": projects}

    @app.post("/runner/enroll")
    async def enroll_runner(request: Request):
        body = await request.json()
        key = str(body.get("setup_key", ""))
        if len(key) < 32 or len(key) > 128:
            raise HTTPException(400, "Runner setup key is invalid or expired")
        try:
            return store.consume_runner_enrollment(key)
        except StateConflict as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/runner/register")
    async def register(body: Registration, identity: Annotated[dict, Depends(runner)]):
        if body.runner_id != identity["id"]:
            raise HTTPException(403, "Runner identity mismatch")
        if body.protocol_version in ("4", PROTOCOL_VERSION) and not body.instance_id:
            raise HTTPException(
                422,
                "Runner process identity is required; upgrade the runner before registering.",
            )
        if body.protocol_version in ("2", "3", "4", PROTOCOL_VERSION):
            from aorch.extensions.schemas import SUPPORTED_ACTION_CONTRACTS

            if any(
                name not in SUPPORTED_ACTION_CONTRACTS
                or version > SUPPORTED_ACTION_CONTRACTS[name]
                for name, version in body.action_contracts.items()
            ):
                raise HTTPException(422, "Runner advertises unsupported action contracts")
        store.heartbeat(identity["id"], body.model_dump())
        if body.protocol_version in ("4", PROTOCOL_VERSION):
            for project in store.claimable_projects(identity["id"]):
                project_row = next(
                    (row for row in store.overview()["projects"] if row["name"] == project), None
                )
                if project_row and project_row["source_type"] == "git" and project_row["status"] == "pending":
                    asyncio.create_task(refresh_project(project))
        return {
            "protocol_version": body.protocol_version,
            "capacity": min(body.capacity, identity["capacity"]),
            "lease_seconds": 30,
        }

    @app.post("/runner/heartbeat")
    async def heartbeat(body: Registration, identity: Annotated[dict, Depends(runner)]):
        return await register(body, identity)

    @app.post("/runner/claim")
    async def claim(request: Request, identity: Annotated[dict, Depends(runner)]):
        for _ in range(10):
            fresh = await asyncio.to_thread(
                store.authenticate, request.headers["authorization"][7:]
            )
            capabilities = json.loads(fresh["capabilities"])
            assignment = await asyncio.to_thread(
                store.claim,
                fresh["id"],
                store.claimable_projects(fresh["id"]),
                min(fresh["capacity"], capabilities.get("capacity", 1)),
                30,
                adapters=capabilities.get("adapters", []),
                resources=capabilities.get("resources", []),
                action_contracts=capabilities.get("action_contracts", {}),
                protocol_version=capabilities.get("protocol_version"),
            )
            if assignment:
                return assignment
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)
        return JSONResponse(None)

    @app.post("/runner/renew")
    def renew(body: AssignmentRequest, identity: Annotated[dict, Depends(runner)]):
        if not store.assignment_live(identity["id"], body.lease_id, body.generation):
            raise StateConflict("Stop requested or lease lost")
        store.renew(identity["id"], body.lease_id, body.generation, lease_seconds=30)
        return {"lease_seconds": 30}

    @app.post("/runner/result")
    def result(body: Completion, identity: Annotated[dict, Depends(runner)]):
        store.complete(identity["id"], body.lease_id, body.generation, body.result)
        return {"accepted": True}

    @app.post("/runner/stopped")
    def stopped(body: StopConfirmation, identity: Annotated[dict, Depends(runner)]):
        store.acknowledge_stop(
            identity["id"], body.lease_id, body.generation, body.uncertain, body.stopped
        )
        return {"accepted": True}

    @app.post("/runner/events")
    def events(body: RunnerEvent, identity: Annotated[dict, Depends(runner)]):
        store.runner_event(
            identity["id"], body.lease_id, body.generation, body.sequence, body.payload
        )
        return {"accepted": True}

    @app.get("/runner/log-request")
    async def next_log_request(identity: Annotated[dict, Depends(runner)]):
        capabilities = json.loads(identity["capabilities"])
        policy = store.pending_log_policy(identity["id"]) if capabilities.get("log_retention") else None
        if policy:
            return {
                "kind": "policy",
                "attempt_id": policy["attempt"],
                "generation": policy["generation"],
                "retained": bool(policy["retained"]),
                "deleted": bool(policy["deleted"]),
            }
        queue = log_queues.setdefault(identity["id"], asyncio.Queue(maxsize=16))
        try:
            return await asyncio.wait_for(queue.get(), timeout=5)
        except TimeoutError:
            return None

    @app.post("/runner/log-response")
    async def log_response(body: dict, identity: Annotated[dict, Depends(runner)]):
        if "policy_generation" in body:
            store.acknowledge_log_policy(
                identity["id"], body["attempt_id"], body["policy_generation"]
            )
            return {"accepted": True}
        pending = log_responses.get(body.get("request_id"))
        if pending is None or pending["runner"] != identity["id"]:
            raise HTTPException(404, "Log request expired")
        if not pending["future"].done():
            pending["future"].set_result(body.get("result", {}))
        return {"accepted": True}

    @app.post("/runner/project-refresh-response")
    async def project_refresh_response(request: Request, identity: Annotated[dict, Depends(runner)]):
        raw = await bounded_body(request, 32 * 1024 * 1024)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(400, "Invalid project refresh response") from error
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid project refresh response")
        pending = refresh_responses.get(body.get("request_id"))
        if pending is None or pending["runner"] != identity["id"]:
            raise HTTPException(404, "Project refresh expired")
        if not pending["future"].done():
            pending["future"].set_result(body)
        return {"accepted": True}

    @app.post("/runner/artifact")
    async def artifact(
        request: Request,
        lease_id: str,
        generation: int,
        identity: Annotated[dict, Depends(runner)],
    ):
        data = await bounded_body(request, 10 * 1024 * 1024)
        digest = hashlib.sha256(data).hexdigest()
        identifier = hashlib.sha256((lease_id + digest).encode()).hexdigest()
        destination = config.artifacts / identifier
        with store.transaction() as db:
            attempt = store._assignment(db, identity["id"], lease_id, generation)
            if attempt[
                "status"
            ] != "running" or not store.assignment_live_without_transaction(
                db, attempt
            ):
                raise StateConflict("Artifact assignment is not live")
            destination.write_bytes(data)
            db.execute(
                "INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)",
                (identifier, attempt["id"], identifier, digest, len(data)),
            )
            store._event(db, attempt["run"], "artifact.created", {"id": identifier})
        return {
            "id": identifier,
            "path": identifier,
            "sha256": digest,
            "size": len(data),
        }

    @app.get("/auth/providers")
    def auth_providers():
        return {
            "email": bool(config.brevo_api_key and not config.brevo_api_key.startswith("DUMMY_")),
            "google": google_enabled,
            "github": github_enabled,
            "local": config.local_auth,
        }

    @app.post("/auth/local")
    def local_login(request: Request):
        if not config.local_auth:
            raise HTTPException(404, "Local sign-in is disabled")
        origin = request.headers.get("origin")
        if not origin or origin.rstrip("/") != config.public_base_url.rstrip("/"):
            raise HTTPException(403, "Local sign-in must originate from the configured loopback URL")
        try:
            user = store.local_auth_user()
        except StateConflict as error:
            raise HTTPException(503, str(error)) from error
        session_token = secrets.token_urlsafe(32)
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        now = time.time()
        with store.transaction() as db:
            db.execute(
                "INSERT INTO browser_sessions(id,token_hash,user_id,expires,created) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), session_hash, user["id"], now + config.session_ttl_seconds, now),
            )
        request.session.clear()
        request.session.update(
            user_id=user["id"], session_hash=session_hash,
            organization_id=user["default_organization_id"], csrf=secrets.token_urlsafe(32),
        )
        return {"authenticated": True, "csrf": request.session["csrf"]}

    @app.get("/auth/me")
    def auth_me(request: Request, _: Annotated[None, Depends(admin)]):
        user = request.state.user
        with store.transaction() as db:
            organizations = [dict(row) for row in db.execute(
                "SELECT o.id,o.slug,o.display_name,m.role FROM organizations o JOIN organization_memberships m ON m.organization_id=o.id WHERE m.user_id=? ORDER BY o.slug",
                (user["id"],),
            )]
        return {"user": {"id": user["id"], "username": user["username"], "display_name": user["display_name"]}, "organizations": organizations, "organization_id": request.state.organization_id, "csrf": request.session["csrf"], "local_auth": config.local_auth}

    @app.post("/auth/organizations")
    def create_organization(request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        slug = str(body.get("slug", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", slug):
            raise HTTPException(422, "Organization name must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
        now, organization_id = time.time(), str(uuid.uuid4())
        try:
            with store.transaction() as db:
                db.execute("INSERT INTO organizations(id,slug,display_name,created_by,created) VALUES(?,?,?,?,?)", (organization_id, slug, str(body.get("display_name") or slug).strip(), request.state.user["id"], now))
                db.execute("INSERT INTO organization_memberships VALUES(?,?,?,?)", (organization_id, request.state.user["id"], "owner", now))
                db.execute("INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)", (request.state.user["id"], organization_id, "organization.created", organization_id, now))
        except Exception as error:
            if "UNIQUE constraint" in str(error):
                raise HTTPException(409, "Organization name is already taken") from error
            raise
        request.session["organization_id"] = organization_id
        return {"id": organization_id, "slug": slug}

    @app.post("/auth/organization/switch")
    def switch_organization(request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        organization_id = str(body.get("organization_id", ""))
        with store.transaction() as db:
            membership = db.execute("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (organization_id, request.state.user["id"])).fetchone()
        if membership is None:
            raise HTTPException(404, "Organization not found")
        request.session["organization_id"] = organization_id
        request.state.organization_id = organization_id
        return {"organization_id": organization_id}

    @app.put("/auth/me/default-organization")
    def set_default_organization(request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        organization_id = str(body.get("organization_id", ""))
        with store.transaction() as db:
            membership = db.execute("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?", (organization_id, request.state.user["id"])).fetchone()
            if membership is None or membership["role"] != "owner":
                raise HTTPException(403, "You must own the organization to set it as the default")
            db.execute("UPDATE users SET default_organization_id=? WHERE id=?", (organization_id, request.state.user["id"]))
        return {"default_organization_id": organization_id}

    @app.delete("/auth/organizations/{organization_id}")
    def delete_organization(organization_id: str, request: Request, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role != "owner":
            raise HTTPException(403, "Only an owner can delete the active organization")
        with store.transaction() as db:
            org = db.execute("SELECT slug FROM organizations WHERE id=?", (organization_id,)).fetchone()
            if org is None:
                raise HTTPException(404, "Organization not found")
            if db.execute("SELECT 1 FROM users WHERE default_organization_id=? LIMIT 1", (organization_id,)).fetchone():
                raise HTTPException(409, "Choose another default organization before deleting this one")
            if db.execute("SELECT 1 FROM project_organizations WHERE organization_id=? LIMIT 1", (organization_id,)).fetchone():
                raise HTTPException(409, "Move or delete this organization's projects first")
            if db.execute("SELECT 1 FROM runners WHERE organization_id=? LIMIT 1", (organization_id,)).fetchone():
                raise HTTPException(409, "Reassign or revoke this organization's runners first")
            db.execute("DELETE FROM organization_invitations WHERE organization_id=?", (organization_id,))
            db.execute("DELETE FROM organization_memberships WHERE organization_id=?", (organization_id,))
            db.execute("DELETE FROM organizations WHERE id=?", (organization_id,))
        request.session.pop("organization_id", None)
        return {"deleted": org["slug"]}

    @app.get("/auth/organizations/{organization_id}/members")
    def organization_members(organization_id: str, request: Request, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Organization administration is restricted")
        with store.transaction() as db:
            return [dict(row) for row in db.execute("SELECT users.id,users.username,users.display_name,user_emails.email,organization_memberships.role,organization_memberships.created FROM organization_memberships JOIN users ON users.id=organization_memberships.user_id LEFT JOIN user_emails ON user_emails.user_id=users.id AND user_emails.is_primary=1 WHERE organization_memberships.organization_id=? ORDER BY users.username", (organization_id,))]

    @app.put("/auth/organizations/{organization_id}/members/{member_id}")
    def update_member(organization_id: str, member_id: str, request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role != "owner":
            raise HTTPException(403, "Only organization owners can change roles")
        role = str(body.get("role", ""))
        if role not in ("owner", "admin", "member"):
            raise HTTPException(422, "Role must be owner, admin, or member")
        with store.transaction() as db:
            member = db.execute("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?", (organization_id, member_id)).fetchone()
            if member is None:
                raise HTTPException(404, "Member not found")
            if member["role"] == "owner" and role != "owner" and db.execute("SELECT count(*) FROM organization_memberships WHERE organization_id=? AND role='owner'", (organization_id,)).fetchone()[0] == 1:
                raise HTTPException(409, "An organization must retain at least one owner")
            db.execute("UPDATE organization_memberships SET role=? WHERE organization_id=? AND user_id=?", (role, organization_id, member_id))
            db.execute("INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)", (request.state.user["id"], organization_id, "membership.role_changed", member_id, time.time()))
        return {"user_id": member_id, "role": role}

    @app.delete("/auth/organizations/{organization_id}/members/{member_id}")
    def remove_member(organization_id: str, member_id: str, request: Request, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Organization administration is restricted")
        with store.transaction() as db:
            member = db.execute("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?", (organization_id, member_id)).fetchone()
            if member is None:
                raise HTTPException(404, "Member not found")
            if member["role"] in ("owner", "admin") and request.state.role != "owner":
                raise HTTPException(403, "Only an owner can remove another administrator")
            if member["role"] == "owner" and db.execute("SELECT count(*) FROM organization_memberships WHERE organization_id=? AND role='owner'", (organization_id,)).fetchone()[0] == 1:
                raise HTTPException(409, "An organization must retain at least one owner")
            db.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (organization_id, member_id))
            db.execute("INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)", (request.state.user["id"], organization_id, "membership.removed", member_id, time.time()))
        return {"removed": member_id}

    @app.post("/auth/organizations/{organization_id}/invitations")
    async def invite_member(organization_id: str, request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Organization administration is restricted")
        email = str(body.get("email", "")).strip().lower()
        role = str(body.get("role", "member"))
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or role not in ("admin", "member"):
            raise HTTPException(422, "Invitation requires a valid email and admin or member role")
        if request.state.role == "admin" and role != "member":
            raise HTTPException(403, "Admins can invite members only")
        if not config.brevo_api_key or config.brevo_api_key.startswith("DUMMY_"):
            raise HTTPException(503, "Email invitations require the Brevo API key")
        now, token = time.time(), secrets.token_urlsafe(32)
        invitation_id = str(uuid.uuid4())
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM organization_memberships JOIN user_emails ON user_emails.user_id=organization_memberships.user_id WHERE organization_id=? AND email=?", (organization_id, email)).fetchone():
                raise HTTPException(409, "This email is already a member")
            db.execute("INSERT INTO organization_invitations(id,organization_id,email,role,token_hash,expires,inviter_id,created) VALUES(?,?,?,?,?,?,?,?)", (invitation_id, organization_id, email, role, hashlib.sha256(token.encode()).hexdigest(), now + config.invitation_ttl_seconds, request.state.user["id"], now))
            organization = db.execute("SELECT slug FROM organizations WHERE id=?", (organization_id,)).fetchone()
        link = f"{config.public_base_url.rstrip('/')}/?invite_token={token}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": config.brevo_api_key, "accept": "application/json"}, json={"sender": {"email": config.email_from, "name": "Runestra"}, "to": [{"email": email}], "subject": f"Invitation to {organization['slug']} on Runestra", "textContent": f"You have been invited to {organization['slug']} as {role}. Sign in with {email} and accept here: {link}"})
        except httpx.HTTPError as error:
            with store.transaction() as db:
                db.execute("UPDATE organization_invitations SET revoked=? WHERE id=?", (time.time(), invitation_id))
            raise HTTPException(502, "Brevo could not send the invitation") from error
        if response.is_error:
            with store.transaction() as db:
                db.execute("UPDATE organization_invitations SET revoked=? WHERE id=?", (time.time(), invitation_id))
            raise HTTPException(502, "Brevo could not send the invitation")
        return {"id": invitation_id, "email": email, "role": role, "expires": now + config.invitation_ttl_seconds}

    @app.post("/auth/invitations/accept")
    def accept_invitation(request: Request, body: dict, _: Annotated[None, Depends(admin)]):
        token_hash = hashlib.sha256(str(body.get("token", "")).encode()).hexdigest()
        now = time.time()
        with store.transaction() as db:
            invitation = db.execute("SELECT * FROM organization_invitations WHERE token_hash=? AND accepted IS NULL AND revoked IS NULL AND expires>?", (token_hash, now)).fetchone()
            email = db.execute("SELECT email FROM user_emails WHERE user_id=? AND verified IS NOT NULL", (request.state.user["id"],)).fetchall()
            if invitation is None or not any(row["email"].lower() == invitation["email"].lower() for row in email):
                raise HTTPException(400, "Invitation is invalid, expired, or belongs to another email")
            db.execute("INSERT OR IGNORE INTO organization_memberships VALUES(?,?,?,?)", (invitation["organization_id"], request.state.user["id"], invitation["role"], now))
            db.execute("UPDATE organization_invitations SET accepted=? WHERE id=?", (now, invitation["id"]))
        return {"accepted": True, "organization_id": invitation["organization_id"]}

    @app.post("/auth/email/request")
    async def request_email_login(request: Request):
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
        if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(422, "Enter a valid email address")
        if not config.brevo_api_key or config.brevo_api_key.startswith("DUMMY_"):
            raise HTTPException(503, "Email sign-in is not configured. Add the Brevo API key to the private secrets file.")
        now = time.time()
        token = secrets.token_urlsafe(32)
        if not request.client or not request.client.host:
            raise HTTPException(400, "Unable to determine client address")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invitation_token = str(body.get("invitation_token", ""))
        invitation_query = ""
        if invitation_token:
            with store.transaction() as db:
                invitation = db.execute("SELECT email FROM organization_invitations WHERE token_hash=? AND accepted IS NULL AND revoked IS NULL AND expires>?", (hashlib.sha256(invitation_token.encode()).hexdigest(), now)).fetchone()
            if invitation is None or invitation["email"].lower() != email:
                raise HTTPException(400, "Invitation is invalid or belongs to another email")
            invitation_query = "&invite_token=" + invitation_token
        store.create_login_token(email, token_hash, now + config.magic_link_ttl_seconds, request.client.host)
        link = f"{config.public_base_url.rstrip('/')}/?login_token={token}{invitation_query}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={"api-key": config.brevo_api_key, "accept": "application/json"},
                    json={
                        "sender": {"email": config.email_from, "name": "Runestra"},
                        "to": [{"email": email}],
                        "subject": "Sign in to Runestra",
                        "textContent": f"Use this one-time link to sign in to Runestra. It expires in {config.magic_link_ttl_seconds // 60} minutes.\n\n{link}\n\nIf you did not request this, ignore this email.",
                    },
                )
        except httpx.HTTPError as error:
            store.discard_login_token(token_hash)
            raise HTTPException(502, "Could not send sign-in email. Check the Brevo API connection.") from error
        if response.is_error:
            store.discard_login_token(token_hash)
            raise HTTPException(502, "Could not send sign-in email. Check Brevo sender and API settings.")
        return {"message": "If the address can sign in, a one-time link has been sent."}

    @app.post("/auth/email/confirm")
    async def confirm_email_login(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != config.public_base_url.rstrip("/"):
            raise HTTPException(403, "Sign-in confirmation must originate from Runestra")
        body = await request.json()
        token = str(body.get("token", ""))
        if len(token) < 32 or len(token) > 128:
            raise HTTPException(400, "Login link is invalid or expired")
        username = str(body.get("username", ""))
        organization = str(body.get("organization", ""))
        if not username or not organization:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with store.transaction() as db:
                login = db.execute(
                    "SELECT email FROM login_tokens WHERE token_hash=? AND used IS NULL AND expires>?",
                    (token_hash, time.time()),
                ).fetchone()
                if login is None:
                    raise HTTPException(400, "Login link is invalid or expired")
                existing_user = db.execute(
                    "SELECT 1 FROM user_emails WHERE email=?",
                    (login["email"],),
                ).fetchone()
            if existing_user is None:
                return {"needs_onboarding": True}
        session_token = secrets.token_urlsafe(32)
        try:
            user, organizations = store.consume_login_token(
                hashlib.sha256(token.encode()).hexdigest(),
                username, str(body.get("display_name", "")), organization,
                hashlib.sha256(session_token.encode()).hexdigest(),
                time.time() + config.session_ttl_seconds,
                config.registration_enabled,
            )
        except StateConflict as error:
            raise HTTPException(400, str(error)) from error
        request.session.clear()
        request.session.update(
            user_id=user["id"], session_hash=hashlib.sha256(session_token.encode()).hexdigest(),
            organization_id=user["default_organization_id"], csrf=secrets.token_urlsafe(32),
        )
        return {"authenticated": True, "csrf": request.session["csrf"], "user": user, "organizations": organizations}

    async def oauth_finish(request: Request, provider: str):
        client = oauth.create_client(provider)
        if client is None:
            raise HTTPException(404, f"{provider.title()} sign-in is not configured")
        try:
            token = await client.authorize_access_token(request)
            if provider == "google":
                profile = token.get("userinfo") or await client.userinfo(token=token)
                subject = str(profile["sub"])
                email = str(profile.get("email", "")).lower()
                verified = profile.get("email_verified") is True
                display = str(profile.get("name", ""))
            else:
                profile = (await client.get("user", token=token)).json()
                subject = str(profile["id"])
                display = str(profile.get("name") or profile.get("login") or "")
                emails = (await client.get("user/emails", token=token)).json()
                address = next((item["email"] for item in emails if item.get("primary") and item.get("verified")), None)
                address = address or next((item["email"] for item in emails if item.get("verified")), None)
                email, verified = str(address or "").lower(), bool(address)
            if not email or not verified:
                raise HTTPException(403, "Provider must return a verified email address")
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(400, "Provider sign-in could not be verified") from error
        now = time.time()
        invitation_hash = request.session.get("oauth_invitation_hash")
        selected_organization_id = None
        with store.transaction() as db:
            identity = db.execute("SELECT user_id FROM auth_identities WHERE provider=? AND subject=?", (provider, subject)).fetchone()
            email_row = db.execute("SELECT user_id,verified FROM user_emails WHERE email=?", (email,)).fetchone()
            linking_user = request.session.get("oauth_link_user") if request.session.get("oauth_link") else None
            if identity:
                user_id = identity["user_id"]
                if linking_user and user_id != linking_user:
                    raise HTTPException(409, "This provider account is linked to another user")
            elif linking_user:
                try:
                    linked_session = store.authenticate_browser_session(request.session.get("session_hash"))
                except StateConflict as error:
                    raise HTTPException(401, "Sign in again before linking a provider") from error
                if linked_session["id"] != linking_user:
                    raise HTTPException(401, "Sign in again before linking a provider")
                if email_row and email_row["user_id"] != linking_user:
                    raise HTTPException(409, "This provider email belongs to another account")
                db.execute("INSERT INTO auth_identities(id,user_id,provider,subject,created) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), linking_user, provider, subject, now))
                if not email_row:
                    db.execute("INSERT INTO user_emails(id,user_id,email,verified,is_primary) VALUES(?,?,?,?,0)", (str(uuid.uuid4()), linking_user, email, now))
                user_id = linking_user
            elif email_row:
                if email_row["verified"] is None:
                    raise HTTPException(409, "This email is reserved. Verify it using the email sign-in link first.")
                raise HTTPException(409, "This email already has an account. Sign in with it, then link this provider from account settings.")
            else:
                if not config.registration_enabled:
                    raise HTTPException(403, "Registration is disabled")
                import unicodedata

                suggested = profile.get("login", "") if provider == "github" else email.split("@", 1)[0]
                username = re.sub(r"[^a-z0-9-]+", "-", unicodedata.normalize("NFKD", str(suggested).lower()).encode("ascii", "ignore").decode()).strip("-")[:50] or provider + "-user"
                if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    username = f"{username}-{secrets.token_hex(3)}"
                slug = username
                if db.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone():
                    slug = f"{username}-{secrets.token_hex(3)}"
                user_id, organization_id = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute("INSERT INTO users(id,username,display_name,default_organization_id,created) VALUES(?,?,?,?,?)", (user_id, username, display or username, organization_id, now))
                db.execute("INSERT INTO user_emails(id,user_id,email,verified,is_primary) VALUES(?,?,?,?,1)", (str(uuid.uuid4()), user_id, email, now))
                db.execute("INSERT INTO organizations(id,slug,display_name,created_by,created) VALUES(?,?,?,?,?)", (organization_id, slug, display or slug, user_id, now))
                db.execute("INSERT INTO organization_memberships VALUES(?,?,?,?)", (organization_id, user_id, "owner", now))
                db.execute("INSERT INTO auth_identities(id,user_id,provider,subject,created) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), user_id, provider, subject, now))
                identity = {"user_id": user_id}
            if invitation_hash:
                invitation = db.execute("SELECT * FROM organization_invitations WHERE token_hash=? AND accepted IS NULL AND revoked IS NULL AND expires>?", (invitation_hash, now)).fetchone()
                if invitation is None or invitation["email"].lower() != email:
                    raise HTTPException(403, "Sign in with the email address that received this invitation")
                db.execute("INSERT OR IGNORE INTO organization_memberships VALUES(?,?,?,?)", (invitation["organization_id"], user_id, invitation["role"], now))
                db.execute("UPDATE organization_invitations SET accepted=? WHERE id=?", (now, invitation["id"]))
                selected_organization_id = invitation["organization_id"]
        session_token = secrets.token_urlsafe(32)
        with store.transaction() as db:
            db.execute("INSERT INTO browser_sessions(id,token_hash,user_id,expires,created) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), hashlib.sha256(session_token.encode()).hexdigest(), user_id, now + config.session_ttl_seconds, now))
            user = dict(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
            organizations = [dict(row) for row in db.execute("SELECT o.id,o.slug,o.display_name,m.role FROM organizations o JOIN organization_memberships m ON m.organization_id=o.id WHERE m.user_id=?", (user_id,)).fetchall()]
        request.session.clear()
        request.session.update(user_id=user_id, session_hash=hashlib.sha256(session_token.encode()).hexdigest(), organization_id=selected_organization_id or user["default_organization_id"], csrf=secrets.token_urlsafe(32))
        return {"csrf": request.session["csrf"], "user": user, "organizations": organizations, "new_account_required": not bool(identity)}

    @app.get("/auth/google/start")
    async def google_start(request: Request, link: bool = False, invitation_token: str = ""):
        client = oauth.create_client("google")
        if client is None:
            raise HTTPException(404, "Google sign-in is not configured")
        if link:
            admin(request)
            request.session["oauth_link"] = True
            request.session["oauth_link_user"] = request.state.user["id"]
        if invitation_token:
            request.session["oauth_invitation_hash"] = hashlib.sha256(invitation_token.encode()).hexdigest()
        return await client.authorize_redirect(request, config.public_base_url.rstrip("/") + "/auth/google/callback", nonce=secrets.token_urlsafe(24))

    @app.get("/auth/google/callback")
    async def google_callback(request: Request):
        await oauth_finish(request, "google")
        return RedirectResponse("/")

    @app.get("/auth/github/start")
    async def github_start(request: Request, link: bool = False, invitation_token: str = ""):
        client = oauth.create_client("github")
        if client is None:
            raise HTTPException(404, "GitHub sign-in is not configured")
        if link:
            admin(request)
            request.session["oauth_link"] = True
            request.session["oauth_link_user"] = request.state.user["id"]
        if invitation_token:
            request.session["oauth_invitation_hash"] = hashlib.sha256(invitation_token.encode()).hexdigest()
        return await client.authorize_redirect(request, config.public_base_url.rstrip("/") + "/auth/github/callback")

    @app.get("/auth/github/callback")
    async def github_callback(request: Request):
        await oauth_finish(request, "github")
        return RedirectResponse("/")

    @app.post("/logout", dependencies=[Depends(admin)])
    def logout(request: Request):
        if request.session.get("session_hash"):
            with store.transaction() as db:
                db.execute("UPDATE browser_sessions SET revoked=? WHERE token_hash=?", (time.time(), request.session["session_hash"]))
        request.session.clear()
        return {"logged_out": True}

    @app.get("/admin/state", dependencies=[Depends(admin)])
    def state(request: Request):
        organization_id = request.state.organization_id
        with store.transaction() as db:
            overview = store.overview()
            visible = {row[0] for row in db.execute("SELECT project FROM project_organizations WHERE organization_id=?", (organization_id,))}
            overview["projects"] = [row for row in overview["projects"] if row["name"] in visible]
            for project in overview["projects"]:
                identity = db.execute("SELECT slug FROM project_organizations WHERE project=?", (project["name"],)).fetchone()
                project["id"] = project["name"]
                project["slug"] = identity["slug"] if identity else project["name"]
            overview["workflows"] = [row for row in overview["workflows"] if row["project"] in visible]
            overview["runs"] = [row for row in overview["runs"] if row["project"] in visible]
            overview["items"] = [row for row in overview["items"] if row["project"] in visible]
            overview["occurrences"] = [row for row in overview["occurrences"] if row["project"] in visible]
            overview["runners"] = [row for row in overview["runners"] if row.get("organization_id") == organization_id and set(json.loads(row["projects"])).intersection(visible)]
            visible_runs = {row["id"] for row in overview["runs"]}
            overview["resource_locks"] = [row for row in overview["resource_locks"] if row["run"] in visible_runs]
            overview["notifications"] = [row for row in store.notification_status() if row["run"] in visible_runs]
            overview["notification_targets"] = store.notification_target_status(organization_id)
            overview["notification_events"] = store.notification_event_preferences(organization_id)
            visible_errors = {key: value for key, value in reload_errors.items() if key in visible}
        return {
            **overview,
            "configuration_errors": visible_errors,
            "csrf": request.session["csrf"],
        }

    @app.put("/admin/notification-targets/{kind}", dependencies=[Depends(admin)])
    async def save_notification_target(kind: str, request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can configure notifications")
        body = await request.json()
        try:
            if not isinstance(body, dict):
                raise TypeError("Request body must be a JSON object")
            url = validate_notification_url(body.get("url"))
            store.set_notification_target(request.state.organization_id, kind, url)
        except (TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        return {"configured": True, "kind": kind}

    @app.delete("/admin/notification-targets/{kind}", dependencies=[Depends(admin)])
    def remove_notification_target(kind: str, request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can configure notifications")
        try:
            removed = store.delete_notification_target(request.state.organization_id, kind)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"removed": removed, "kind": kind}

    @app.put("/admin/notification-events", dependencies=[Depends(admin)])
    async def save_notification_events(request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Only organization administrators can configure notifications")
        body = await request.json()
        try:
            if not isinstance(body, dict):
                raise TypeError("Request body must be a JSON object")
            events = body.get("events")
            store.set_notification_event_preferences(request.state.organization_id, events)
        except (TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        return {"events": store.notification_event_preferences(request.state.organization_id)}

    @app.get("/admin/runs/{run}", dependencies=[Depends(admin)])
    def details(run: str, request: Request):
        require_run_access(request, run)
        detail = store.get_run(run)
        with store.transaction() as db:
            package = json.loads(db.execute(
                "SELECT snapshot FROM revisions WHERE id=?", (detail["revision"],)
            ).fetchone()[0])
            detail["definition"] = package["configuration"]["workflows"][detail["workflow"]]
            detail["repository"] = package.get("repository")
            detail["attempts"] = [
                dict(row)
                for row in db.execute("SELECT * FROM attempts WHERE run=? ORDER BY rowid", (run,))
            ]
            detail["events"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE run=? ORDER BY id", (run,)
                )
            ]
            detail["status_history"] = [dict(row) for row in db.execute(
                "SELECT status,created FROM run_status_history WHERE run=? ORDER BY created,id", (run,)
            )]
            terminal_statuses = ("succeeded", "failed", "cancelled", "needs_input", "needs_attention")
            detail["run_seconds"] = (
                max(0, (detail["finished"] if detail["finished"] is not None else time.time()) - detail["created"])
                if detail["status"] not in terminal_statuses or detail["finished"] is not None
                else None
            )
            attempts_by_id = {attempt["id"]: attempt for attempt in detail["attempts"]}
            started_at = {}
            completed_at = {}
            execution_started_at = {}
            execution_completed_at = {}
            pending_by_step = {}
            for event in detail["events"]:
                try:
                    payload = json.loads(event["payload"] or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if event["kind"] == "step.claimed":
                    attempt_id = payload.get("attempt")
                    attempt = attempts_by_id.get(attempt_id)
                    if attempt:
                        started_at[attempt_id] = event["created"]
                        pending_by_step.setdefault(attempt["step"], []).append(attempt_id)
                elif event["kind"] == "runner.event":
                    attempt_id = payload.get("attempt")
                    data = payload.get("data") or {}
                    if attempt_id in attempts_by_id and isinstance(data, dict):
                        if data.get("kind") == "step.started":
                            execution_started_at.setdefault(attempt_id, event["created"])
                        elif data.get("kind") == "step.completed":
                            execution_completed_at[attempt_id] = event["created"]
                elif event["kind"] == "step.completed":
                    attempt_id = payload.get("attempt")
                    step_name = payload.get("step")
                    if attempt_id in attempts_by_id:
                        completed_at[attempt_id] = event["created"]
                        pending = pending_by_step.get(step_name, [])
                        if attempt_id in pending:
                            pending.remove(attempt_id)
                    elif step_name:
                        pending = pending_by_step.get(step_name, [])
                        if pending:
                            completed_at[pending.pop(0)] = event["created"]
            now = time.time()
            detail["measured_at"] = now
            execution_timing_complete = True
            for attempt in detail["attempts"]:
                start = started_at.get(attempt["id"])
                end = completed_at.get(attempt["id"])
                attempt["started_at"] = start
                attempt["completed_at"] = end
                execution_start = execution_started_at.get(attempt["id"])
                execution_end = execution_completed_at.get(attempt["id"], end)
                attempt["execution_started_at"] = execution_start
                attempt["execution_completed_at"] = execution_end
                attempt["execution_seconds"] = (
                    max(0, (execution_end if execution_end is not None else now) - execution_start)
                    if execution_start is not None and (execution_end is not None or attempt["status"] in ("running", "reconciling"))
                    else None
                )
                if execution_start is None or (
                    execution_end is None and attempt["status"] not in ("running",)
                ):
                    execution_timing_complete = False
                attempt["duration_seconds"] = (
                    max(0, (end if end is not None else now) - start)
                    if start is not None and (end is not None or attempt["status"] in ("running", "reconciling"))
                    else None
                )
            # Count overlapping attempts once. Reconciliation is tracked from
            # status transitions and kept separate from waiting and execution.
            intervals = sorted(
                (attempt["execution_started_at"], attempt["execution_completed_at"] or now)
                for attempt in detail["attempts"] if attempt["execution_seconds"] is not None
            )
            execution_seconds = 0
            interval_start = interval_end = None
            for start, end in intervals:
                if interval_start is None:
                    interval_start, interval_end = start, end
                elif start <= interval_end:
                    interval_end = max(interval_end, end)
                else:
                    execution_seconds += interval_end - interval_start
                    interval_start, interval_end = start, end
            if interval_start is not None:
                execution_seconds += interval_end - interval_start
            reconciling_seconds = 0
            status_history = detail["status_history"]
            for index, entry in enumerate(status_history):
                if entry["status"] == "reconciling":
                    end = status_history[index + 1]["created"] if index + 1 < len(status_history) else (detail["finished"] or now)
                    reconciling_seconds += max(0, end - entry["created"])
            detail["execution_seconds"] = execution_seconds if execution_timing_complete else None
            detail["reconciliation_seconds"] = reconciling_seconds
            detail["waiting_seconds"] = (
                max(0, detail["run_seconds"] - execution_seconds - reconciling_seconds)
                if detail["run_seconds"] is not None and execution_timing_complete else None
            )
            detail["artifacts"] = [
                dict(row)
                for row in db.execute(
                    "SELECT artifacts.* FROM artifacts JOIN attempts ON artifacts.attempt=attempts.id WHERE attempts.run=?",
                    (run,),
                )
            ]
        return detail

    @app.get("/admin/runs/{run}/attempts/{attempt}/logs", dependencies=[Depends(admin)])
    async def attempt_logs(run: str, attempt: str, request: Request, after: int = 0, limit: int = 16, view: str = "activity"):
        require_run_access(request, run)
        with store.transaction() as db:
            attempt_project = db.execute("SELECT runs.project FROM attempts JOIN runs ON runs.id=attempts.run WHERE attempts.id=? AND attempts.run=?", (attempt, run)).fetchone()
        if attempt_project is None:
            raise HTTPException(404, "Attempt not found")
        if after < 0 or limit < 1 or limit > 16 or view not in ("activity", "raw"):
            raise HTTPException(400, "Invalid log query")
        with store.transaction() as db:
            row = db.execute("SELECT runner FROM attempts WHERE id=? AND run=?", (attempt, run)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown attempt")
        runner_id = row["runner"]
        if runner_id is None:
            return {"available": False, "events": [], "next": after, "message": "No runner was recorded for this attempt."}
        if runner_id not in log_queues:
            return {"available": False, "events": [], "next": after, "message": "Runner is offline or logs have expired."}
        request_id = secrets.token_urlsafe(18)
        future = asyncio.get_running_loop().create_future()
        log_responses[request_id] = {"runner": runner_id, "future": future}
        try:
            try:
                log_queues[runner_id].put_nowait({"request_id": request_id, "attempt_id": attempt, "after": after, "limit": limit, "view": view})
            except asyncio.QueueFull as error:
                raise HTTPException(503, "Runner log request queue is full") from error
            return await asyncio.wait_for(future, timeout=20)
        except TimeoutError:
            return {"available": False, "events": [], "next": after, "message": "Runner did not respond to the log request."}
        finally:
            log_responses.pop(request_id, None)

    @app.post("/admin/runs", dependencies=[Depends(admin)])
    async def enqueue(request: Request):
        body = await request.json()
        project = body["project"]
        require_project_access(request, project)
        source = next((row for row in store.overview()["projects"] if row["name"] == project), None)
        if source and source["source_type"] in ("git", "local") and source["status"] != "ready":
            raise HTTPException(409, f"Project is not ready: {source['status']}")
        try:
            return {
                "run_id": store.enqueue(
                    project, body["workflow"], body.get("inputs", {})
                )
            }
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.put("/admin/workflows/{project}/{workflow}/retention", dependencies=[Depends(admin)])
    async def set_workflow_retention(project: str, workflow: str, request: Request):
        require_project_access(request, project)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Retention changes are restricted")
        body = await request.json()
        try:
            return store.set_workflow_retention(project, workflow, body.get("retention_count"))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.put("/admin/runs/{run}/retention", dependencies=[Depends(admin)])
    async def set_run_retention(run: str, request: Request):
        require_run_access(request, run)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Retention changes are restricted")
        body = await request.json()
        try:
            return store.set_keep_forever(run, body.get("keep_forever"))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete("/admin/runs/{run}", dependencies=[Depends(admin)])
    async def delete_run(run: str, request: Request):
        require_run_access(request, run)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Run deletion is restricted")
        return store.delete_run(run)

    @app.post("/admin/projects", dependencies=[Depends(admin)])
    async def add_project(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip().lower()
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Project creation is restricted")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
            raise HTTPException(422, "Project name must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
        try:
            source = GitProjectSource.model_validate({"git": body.get("git", ""), "ref": body.get("ref", "HEAD")})
            validate_git_host(source)
        except Exception as error:
            raise HTTPException(422, str(error)) from error
        project_id = str(uuid.uuid4())
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM project_organizations WHERE organization_id=? AND slug=?", (request.state.organization_id, name)).fetchone():
                raise HTTPException(409, "A project with that name already exists in this organization")
            db.execute("INSERT INTO project_organizations(project,organization_id,slug) VALUES(?,?,?)", (project_id, request.state.organization_id, name))
        store.set_project(project_id, "git", source.git, source.ref, None, None, "pending")
        source_config[project_id] = source
        return {"project_id": project_id, "slug": name, "status": "pending"}

    @app.post("/admin/projects/{name}/refresh", dependencies=[Depends(admin)])
    async def refresh(name: str, request: Request, force: bool = False):
        require_project_access(request, name)
        if name not in source_config:
            raise HTTPException(404, "Unknown project")
        return await refresh_project(name, force=force)

    @app.delete("/admin/projects/{name}", dependencies=[Depends(admin)])
    def delete_project(name: str, request: Request):
        require_project_access(request, name)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Project deletion is restricted")
        project = next(
            (row for row in store.overview()["projects"] if row["name"] == name),
            None,
        )
        try:
            result = store.delete_project(name)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        source_config.pop(name, None)
        reload_errors.pop(name, None)
        if project and project["source_type"] == "git" and project["path"]:
            cache_root = (config.database.parent / ".aorch-projects").resolve()
            checkout = Path(project["path"]).resolve()
            if checkout != cache_root and checkout.is_relative_to(cache_root):
                shutil.rmtree(checkout, ignore_errors=True)
        return result

    @app.post("/admin/runs/{run}/cancel", dependencies=[Depends(admin)])
    def cancel(run: str, request: Request):
        require_run_access(request, run)
        store.cancel(run)
        return {"accepted": True}

    @app.post("/admin/runs/{run}/steps/{step}/approval", dependencies=[Depends(admin)])
    async def decide_approval(run: str, step: str, request: Request):
        require_run_access(request, run)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Approval decisions are restricted to organization administrators")
        body = await request.json()
        try:
            return store.decide_approval(
                run,
                step,
                request.state.user["id"],
                request.state.role,
                body.get("decision"),
                body.get("note", ""),
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/admin/runs/{run}/answers", dependencies=[Depends(admin)])
    async def answer_run(run: str, request: Request):
        require_run_access(request, run)
        body = await request.json()
        try:
            resumed = store.answer_needs_input(run, body.get("answers"))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"run_id": resumed}

    @app.post("/admin/runs/{run}/resolve", dependencies=[Depends(admin)])
    async def resolve(run: str, request: Request):
        require_run_access(request, run)
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Run resolution is restricted")
        body = await request.json()
        store.resolve(
            run,
            body.get("status"),
            body.get("processes_stopped", False),
            body.get("effects_reviewed", False),
            body.get("note", ""),
        )
        return {"accepted": True}

    @app.post("/admin/reload", dependencies=[Depends(admin)])
    async def reload(request: Request):
        if request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Configuration refresh is restricted")
        for name in source_config:
            try:
                require_project_access(request, name)
            except HTTPException:
                continue
            await refresh_project(name)
        with store.transaction() as db:
            visible = {row["project"] for row in db.execute("SELECT project FROM project_organizations WHERE organization_id=?", (request.state.organization_id,))}
        return {"errors": {key: value for key, value in reload_errors.items() if key in visible}}

    @app.get("/auth/organizations/{organization_id}/invitations")
    def list_invitations(organization_id: str, request: Request, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Organization administration is restricted")
        with store.transaction() as db:
            return [dict(row) for row in db.execute("SELECT id,email,role,expires,created FROM organization_invitations WHERE organization_id=? AND accepted IS NULL AND revoked IS NULL ORDER BY created DESC", (organization_id,))]

    @app.delete("/auth/organizations/{organization_id}/invitations/{invitation_id}")
    def revoke_invitation(organization_id: str, invitation_id: str, request: Request, _: Annotated[None, Depends(admin)]):
        if organization_id != request.state.organization_id or request.state.role not in ("owner", "admin"):
            raise HTTPException(403, "Organization administration is restricted")
        with store.transaction() as db:
            result = db.execute("UPDATE organization_invitations SET revoked=? WHERE id=? AND organization_id=? AND accepted IS NULL AND revoked IS NULL", (time.time(), invitation_id, organization_id))
            if result.rowcount != 1:
                raise HTTPException(404, "Invitation not found")
        return {"revoked": invitation_id}

    @app.get("/auth/identities")
    def list_identities(request: Request, _: Annotated[None, Depends(admin)]):
        with store.transaction() as db:
            return [dict(row) for row in db.execute("SELECT provider,subject,created FROM auth_identities WHERE user_id=? ORDER BY provider", (request.state.user["id"],))]

    @app.get("/admin/artifacts/{identifier}", dependencies=[Depends(admin)])
    def download(identifier: str, request: Request):
        with store.transaction() as db:
            row = db.execute(
                "SELECT artifacts.path,runs.project FROM artifacts JOIN attempts ON attempts.id=artifacts.attempt JOIN runs ON runs.id=attempts.run WHERE artifacts.id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown artifact")
        require_project_access(request, row["project"])
        return FileResponse(
            config.artifacts / row[0],
            media_type="application/octet-stream",
            filename=identifier,
        )

    @app.get("/admin/events", dependencies=[Depends(admin)])
    async def stream(request: Request, after: int = 0):
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError as error:
            raise HTTPException(400, "Invalid event cursor") from error

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                try:
                    admin(request)
                except HTTPException:
                    return
                rows = await asyncio.to_thread(store.events, cursor)
                for row in rows:
                    cursor = row["id"]
                    if row.get("run"):
                        try:
                            require_run_access(request, row["run"])
                        except HTTPException:
                            continue
                    yield f"id: {cursor}\ndata: {json.dumps(row)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/", response_class=HTMLResponse)
    def index():
        from aorch.engine.web import page

        return page()

    return app
