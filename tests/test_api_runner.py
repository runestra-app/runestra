import asyncio
import hashlib
import hmac
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from aorch.config import snapshot
from aorch.config.service import EngineConfig, RunnerConfig
from aorch.contracts import StepResult
from aorch.engine.api import create_app
from aorch.runner.client import Runner, redact_stream_chunk
from aorch.runner.executor import (
    environment,
    prepare_repository,
    project_environment_context,
)
from aorch.runner.git import git_environment
from aorch.runner.process import run_process

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/local-actions"


def test_runner_prepares_the_pinned_repository_commit(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("pinned content\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "newer.md").write_text("main moved after snapshot\n")
    subprocess.run(["git", "-C", str(repo), "add", "newer.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "newer main commit",
        ],
        check=True,
        capture_output=True,
    )
    config = RunnerConfig(
        id="test", token_file=tmp_path / "token", workspace_root=tmp_path / "workspaces",
        allow_file_git_sources=True,
    )
    workspace = tmp_path / "run"
    workspace.mkdir()
    code_dir = asyncio.run(
        prepare_repository(
            {"git": str(repo), "ref": "main", "commit": commit},
            workspace,
            config,
            asyncio.Event(),
        )
    )
    checked_out = subprocess.run(
        ["git", "-C", str(code_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert checked_out == commit
    assert (code_dir / "README.md").read_text() == "pinned content\n"
    assert not (code_dir / ".aorch-commit").exists()


def secret(path, value="s" * 40):
    path.write_text(value)
    path.chmod(0o600)
    return path


def application(tmp_path):
    app = create_app(
        EngineConfig(
            database=tmp_path / "engine.sqlite",
            artifacts=tmp_path / "artifacts",
            admin_token_file=secret(tmp_path / "admin"),
            session_key_file=secret(tmp_path / "session", "k" * 40),
        )
    )
    app.state.store.register("example", snapshot(EXAMPLE))
    app.state.store.set_project(
        "example", "local", str(EXAMPLE), path=str(EXAMPLE), status="ready"
    )
    app.state.store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    return app


def browser_login(client, store):
    token = "test-login-token-that-is-long-enough-123456"
    store.create_login_token(
        "owner@example.test",
        hashlib.sha256(token.encode()).hexdigest(),
        time.time() + 600,
    )
    response = client.post(
        "/auth/email/confirm",
        json={
            "token": token,
            "username": "owner",
            "display_name": "Owner",
            "organization": "owner-org",
        },
    )
    assert response.status_code == 200
    return response


def test_runner_enrollment_uses_server_generated_identity(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    with TestClient(app) as client:
        login = browser_login(client, store)
        response = client.post(
            "/admin/runners",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={"id": "known-runner", "name": "Build machine", "projects": ["example"]},
        )
        assert response.status_code == 200
        setup = response.json()
        runner_id = setup["id"]
        assert len(runner_id) >= 32
        assert runner_id != "known-runner"
        assert setup["name"] == "Build machine"
        command = shlex.split(setup["command"])
        setup_key = command[command.index("--setup-key") + 1]

        enrollment = client.post("/runner/enroll", json={"setup_key": setup_key})
        assert enrollment.status_code == 200
        assert enrollment.json()["runner_id"] == runner_id
        assert client.post("/runner/enroll", json={"setup_key": setup_key}).status_code == 400
        with store.read_transaction() as db:
            registered = db.execute("SELECT id,name FROM runners WHERE id=?", (runner_id,)).fetchone()
        assert dict(registered) == {"id": runner_id, "name": "Build machine"}


def test_stream_redaction_holds_split_secret_until_scrubbed():
    first, tail = redact_stream_chunk("", "prefix top-se", ["top-secret"])
    second, tail = redact_stream_chunk(tail, "cret suffix", ["top-secret"])
    output = first + second + tail
    assert "top-secret" not in output
    assert "[REDACTED]" in output


def test_runner_redacts_profile_and_api_credential_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_SECRET", "custom-secret-value")
    token_path = secret(tmp_path / "runner.token", "runner-secret-value-0123456789abcdef")
    config = RunnerConfig(
        id="runner",
        token_file=token_path,
        api_credentials={"claude": "CUSTOM_SECRET"},
    )
    runner = Runner(config)
    package = {"configuration": {"profiles": {"review": {"credential_env": "CUSTOM_SECRET"}}}}
    cleaned = runner.scrub_log(
        {"message": "custom-secret-value and runner-secret-value-0123456789abcdef"}, package
    )
    assert cleaned["message"] == "[REDACTED] and [REDACTED]"


def test_project_environment_api_never_returns_secret_values(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        login = browser_login(client, app.state.store)
        csrf = {"X-CSRF-Token": login.json()["csrf"]}
        saved = client.put(
            "/admin/projects/example/environment",
            headers=csrf,
            json={"name": "DEPLOY_TOKEN", "value": "top-secret-value", "secret": True},
        )
        assert saved.status_code == 200
        listed = client.get("/admin/projects/example/environment").json()
        assert listed == {"variables": [{"name": "DEPLOY_TOKEN", "secret": True, "value": None}]}
        assert "top-secret-value" not in listed.__repr__()
        with app.state.store.read_transaction() as db:
            audit = db.execute(
                "SELECT action, target_id FROM audit_events WHERE action='project.environment.set'"
            ).fetchone()
        assert tuple(audit) == ("project.environment.set", "example:DEPLOY_TOKEN")
        assert "top-secret-value" not in str(tuple(audit))
        assert client.put(
            "/admin/projects/example/environment",
            headers=csrf,
            json={"name": "AORCH_RUNNER_TOKEN", "value": "bad", "secret": False},
        ).status_code == 422


def test_project_environment_is_injected_and_masked(tmp_path):
    config = RunnerConfig(id="runner", token_file=secret(tmp_path / "runner.token"))
    token = project_environment_context.set(
        {"PUBLIC_ENDPOINT": "https://example.test", "DEPLOY_TOKEN": "top-secret-value"}
    )
    try:
        child_environment = environment(config)
    finally:
        project_environment_context.reset(token)
    assert child_environment["PUBLIC_ENDPOINT"] == "https://example.test"
    assert child_environment["DEPLOY_TOKEN"] == "top-secret-value"
    runner = Runner(config)
    assignment = {
        "project_environment": {"DEPLOY_TOKEN": "top-secret-value"},
        "project_secret_names": ["DEPLOY_TOKEN"],
    }
    cleaned = runner.scrub_log({"message": "found top-secret-value"}, assignment=assignment)
    assert cleaned["message"] == "found [REDACTED]"


def test_project_registration_rejects_unapproved_git_hosts(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        login = browser_login(client, app.state.store)
        response = client.post(
            "/admin/projects",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={"name": "internal", "git": "https://127.0.0.1/repo.git"},
        )
    assert response.status_code == 422
    assert "allowed host" in response.json()["detail"]


def test_project_registration_accepts_ssh_sources_for_runner_refresh(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        login = browser_login(client, app.state.store)
        response = client.post(
            "/admin/projects",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={"name": "private-source", "git": "git@github.com:acme/private.git", "ref": "main"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_runner_refreshes_project_and_returns_configuration_snapshot(tmp_path):
    initial = application(tmp_path)
    store = initial.state.store
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    store.set_project("example", "git", "git@github.com:acme/private.git", "main", status="pending")
    runner_token = store.approve_runner(
        "refresh-runner", ["example"], organization_id=owner["organization_id"]
    )
    store.heartbeat("refresh-runner", {"protocol_version": "5", "capacity": 1})
    app = create_app(
        EngineConfig(
            database=tmp_path / "engine.sqlite",
            artifacts=tmp_path / "artifacts",
            admin_token_file=secret(tmp_path / "admin"),
            session_key_file=secret(tmp_path / "session", "k" * 40),
        )
    )
    package = snapshot(EXAMPLE)
    with TestClient(app) as client:
        login = browser_login(client, app.state.store)
        csrf = {"X-CSRF-Token": login.json()["csrf"]}
        runner_headers = {"Authorization": f"Bearer {runner_token}"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(
                client.post,
                "/admin/projects/example/refresh",
                headers=csrf,
            )
            task = client.get("/runner/log-request", headers=runner_headers)
            assert task.status_code == 200
            assert task.json()["kind"] == "project-refresh"
            reply = client.post(
                "/runner/project-refresh-response",
                headers=runner_headers,
                json={"request_id": task.json()["request_id"], "commit": "a" * 40, "package": package},
            )
            assert reply.status_code == 200
            refreshed = refresh.result(timeout=5)
    assert refreshed.status_code == 200
    assert refreshed.json() == {"status": "ready", "commit": "a" * 40}
    assert app.state.store.overview()["projects"][0]["status"] == "ready"


def test_git_authentication_files_are_private_and_temporary(tmp_path):
    key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    env = git_environment(
        {
            "RUNESTRA_GIT_SSH_PRIVATE_KEY": key,
            "RUNESTRA_GIT_KNOWN_HOSTS": "github.com ssh-ed25519 AAAA",
            "DEPLOY_TOKEN": "another-secret",
        },
        "git@github.com:acme/private.git",
        tmp_path,
        project_names={"RUNESTRA_GIT_SSH_PRIVATE_KEY", "RUNESTRA_GIT_KNOWN_HOSTS", "DEPLOY_TOKEN"},
    )
    key_path = tmp_path / "ssh-key"
    known_hosts_path = tmp_path / "known_hosts"
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert known_hosts_path.stat().st_mode & 0o777 == 0o600
    assert key not in env.values()
    assert "DEPLOY_TOKEN" not in env
    assert "StrictHostKeyChecking=yes" in env["GIT_SSH_COMMAND"]


def test_existing_owner_magic_link_skips_onboarding_prompts(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    token = "existing-owner-login-token-long-enough-123"
    store.create_login_token(
        "owner@example.test",
        hashlib.sha256(token.encode()).hexdigest(),
        time.time() + 600,
    )

    with TestClient(app) as client:
        response = client.post("/auth/email/confirm", json={"token": token})

        assert response.status_code == 200
        assert response.json()["authenticated"] is True
        assert client.get("/auth/me").json()["user"]["username"] == "owner"


def test_new_email_link_requests_onboarding_without_consuming_token(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    token = "new-account-login-token-long-enough-123"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    store.create_login_token("new@example.test", token_hash, time.time() + 600)

    with TestClient(app) as client:
        response = client.post("/auth/email/confirm", json={"token": token})

        assert response.status_code == 200
        assert response.json() == {"needs_onboarding": True}
        with store.transaction() as db:
            assert db.execute(
                "SELECT used FROM login_tokens WHERE token_hash=?", (token_hash,)
            ).fetchone()[0] is None


def test_runner_registration_negotiates_action_contract_versions(tmp_path):
    app = application(tmp_path)
    token = app.state.store.approve_runner("runner", ["example"])
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert client.get("/health").json()["protocol_version"] == "5"
        legacy = client.post(
            "/runner/register",
            json={"protocol_version": "1", "runner_id": "runner", "adapters": ["action"]},
            headers=headers,
        )
        assert legacy.status_code == 200
        assert legacy.json()["protocol_version"] == "1"
        current = client.post(
            "/runner/register",
            json={
                "protocol_version": "2",
                "runner_id": "runner",
                "adapters": ["action"],
                "action_contracts": {"git.branch": 2},
            },
            headers=headers,
        )
        assert current.status_code == 200
        assert current.json()["protocol_version"] == "2"
        latest = client.post(
            "/runner/register",
            json={
                "protocol_version": "5",
                "runner_id": "runner",
                "instance_id": "test-instance",
                "adapters": ["action", "codex"],
            },
            headers=headers,
        )
        assert latest.status_code == 200
        assert latest.json()["protocol_version"] == "5"
        unsupported = client.post(
            "/runner/register",
            json={
                "protocol_version": "2",
                "runner_id": "runner",
                "action_contracts": {"git.branch": 99},
            },
            headers=headers,
        )
        assert unsupported.status_code == 422
        invalid_legacy = client.post(
            "/runner/register",
            json={
                "protocol_version": "1",
                "runner_id": "runner",
                "action_contracts": {"git.branch": 2},
            },
            headers=headers,
        )
        assert invalid_legacy.status_code == 422


def test_auth_session_csrf_rotation_and_events(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    token = store.approve_runner("runner", ["example"])
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'href="/static/styles.css"' in page.text
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/admin/state").status_code == 401
        assert (
            client.post("/runner/register", json={"runner_id": "runner"}).status_code
            == 401
        )
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/admin/state", headers=headers).status_code == 401
        assert (
            client.post(
                "/runner/register", json={"runner_id": "other"}, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/runner/register",
                json={"runner_id": "runner", "adapters": ["action", "shell"]},
                headers=headers,
            ).status_code
            == 200
        )
        login = browser_login(client, store)
        csrf = {"X-CSRF-Token": login.json()["csrf"]}
        assert (
            client.post(
                "/admin/runs", json={"project": "example", "workflow": "commands"}
            ).status_code
            == 403
        )
        run = client.post(
            "/admin/runs",
            json={"project": "example", "workflow": "commands"},
            headers=csrf,
        ).json()["run_id"]
        claim = client.post("/runner/claim", headers=headers).json()
        identity = {"lease_id": claim["lease_id"], "generation": claim["generation"]}
        event = {**identity, "sequence": 1, "payload": {"text": "hello"}}
        assert (
            client.post("/runner/events", json=event, headers=headers).status_code
            == 200
        )
        assert (
            client.post("/runner/events", json=event, headers=headers).status_code
            == 200
        )
        assert (
            client.post(
                "/runner/events", json={**event, "payload": {}}, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/runner/artifact", params=identity, content=b"report", headers=headers
            ).status_code
            == 200
        )
        assert client.post(f"/admin/runs/{run}/cancel", headers=csrf).status_code == 200
        assert (
            client.post("/runner/renew", json=identity, headers=headers).status_code
            == 409
        )
        assert (
            client.post(
                "/runner/stopped",
                json={**identity, "uncertain": False},
                headers=headers,
            ).status_code
            == 200
        )
        assert store.get_run(run)["status"] == "cancelled"
        assert client.delete(f"/admin/runs/{run}", headers=csrf).status_code == 200
        assert run not in {row["id"] for row in store.overview()["runs"]}
        store.approve_runner("runner", ["example"])
        assert (
            client.post(
                "/runner/register", json={"runner_id": "runner"}, headers=headers
            ).status_code
            == 401
        )


def test_admin_can_delete_project_and_its_persisted_workflows(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    with TestClient(app) as client:
        login = browser_login(client, store)
        csrf = {"X-CSRF-Token": login.json()["csrf"]}

        response = client.delete("/admin/projects/example", headers=csrf)

        assert response.status_code == 200
        assert response.json() == {"deleted": "example"}
        assert not store.overview()["projects"]
        assert not store.overview()["workflows"]


def test_real_http_runner_two_commands(tmp_path):
    async def scenario():
        app = application(tmp_path)
        store = app.state.store
        token = store.approve_runner("runner", ["example"])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        server_task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        config = RunnerConfig(
            id="runner",
            engine_url=f"http://127.0.0.1:{port}",
            token_file=secret(tmp_path / "runner.token", token),
            workspace_root=tmp_path / "workspaces",
            journal=tmp_path / "journal.sqlite",
        )
        runner = Runner(config)
        stop = asyncio.Event()
        runner_task = asyncio.create_task(runner.serve(stop))
        try:
            run = store.enqueue("example", "commands")
            for _ in range(200):
                state = store.get_run(run)
                if state["status"] == "succeeded":
                    break
                await asyncio.sleep(0.05)
            assert state["status"] == "succeeded"
            with store.read_transaction() as db:
                attempts = [row[0] for row in db.execute("SELECT id FROM attempts WHERE run=?", (run,))]
            log_events = [
                event
                for attempt in attempts
                for event in runner.logs.read(attempt, limit=16, view="raw")["events"]
            ]
            output = "".join(event.get("message", "") for event in log_events)
            assert "first" in output
            assert "second" in output
            assert runner.journal.pending() == []
        finally:
            stop.set()
            await asyncio.wait_for(runner_task, 15)
            server.should_exit = True
            await server_task

    asyncio.run(scenario())


def test_timeout_stops_process_group(tmp_path):
    async def scenario():
        script = "import subprocess,time; p=subprocess.Popen(['sleep','60']); open('child.pid','w').write(str(p.pid)); time.sleep(60)"
        with pytest.raises(TimeoutError):
            await run_process(
                [sys.executable, "-c", script],
                tmp_path,
                os.environ.copy(),
                "",
                0.3,
                asyncio.Event(),
            )
        pid = int((tmp_path / "child.pid").read_text())
        # A terminated child can briefly be a zombie until the OS reaps it.
        import subprocess

        result = await asyncio.to_thread(
            subprocess.run,
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z")

    asyncio.run(scenario())


def test_output_limit_stops_process(tmp_path):
    async def scenario():
        with pytest.raises(ValueError, match="limit"):
            await run_process(
                [sys.executable, "-c", "print('x'*10000)"],
                tmp_path,
                os.environ.copy(),
                "",
                5,
                asyncio.Event(),
                limit=100,
            )

    asyncio.run(scenario())


def test_signed_webhook_is_deduplicated_and_survives_engine_restart(tmp_path):
    secret_value = "w" * 40
    project = tmp_path / "hook-project"
    config_dir = project / ".aorch"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        """version: 1
name: hook-project
workflows:
  receive:
    input_schema:
      type: object
      properties:
        issue:
          type: object
          properties:
            number: {type: integer}
          required: [number]
          additionalProperties: false
      required: [issue]
      additionalProperties: false
    steps:
      accept:
        type: shell
        command: 'printf accepted'
"""
    )
    database = tmp_path / "engine.sqlite"
    artifacts = tmp_path / "artifacts"
    session_key = secret(tmp_path / "session-key", "k" * 40)
    hook = {
        "provider": "hmac",
        "secret": secret_value,
        "project": "hook-project",
        "workflow": "receive",
        "input_mapping": {"issue": "issue"},
    }

    def make_client():
        app = create_app(
            EngineConfig(
                database=database,
                artifacts=artifacts,
                session_key_file=session_key,
                webhook_triggers={"issue-hook": hook},
            )
        )
        if not app.state.store.overview()["projects"]:
            app.state.store.register("hook-project", snapshot(project))
            app.state.store.set_project(
                "hook-project", "local", str(project), path=str(project), status="ready"
            )
        return TestClient(app)

    raw = json.dumps({"issue": {"number": 17}}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = "sha256=" + hmac.new(
        secret_value.encode(), timestamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Runestra-Timestamp": timestamp,
        "X-Runestra-Event-Id": "delivery-17",
        "X-Runestra-Signature": signature,
    }
    with make_client() as client:
        first = client.post("/hooks/issue-hook", content=raw, headers=headers)
        assert first.status_code == 200
        assert first.json()["created"] is True
        run_id = first.json()["run_id"]
    with make_client() as client:
        repeated = client.post("/hooks/issue-hook", content=raw, headers=headers)
        assert repeated.status_code == 200
        assert repeated.json() == {"accepted": True, "created": False, "run_id": run_id}
        changed = json.dumps({"issue": {"number": 18}}, separators=(",", ":")).encode()
        changed_signature = "sha256=" + hmac.new(
            secret_value.encode(), timestamp.encode() + b"." + changed, hashlib.sha256
        ).hexdigest()
        changed_response = client.post(
            "/hooks/issue-hook",
            content=changed,
            headers={**headers, "X-Runestra-Signature": changed_signature},
        )
        assert changed_response.status_code == 422
        invalid = client.post(
            "/hooks/issue-hook",
            content=raw,
            headers={**headers, "X-Runestra-Signature": "sha256=" + "0" * 64},
        )
        assert invalid.status_code == 401


def test_approval_route_enforces_csrf_and_records_owner_decision(tmp_path):
    app = application(tmp_path)
    store = app.state.store
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["steps"] = {
        "verify": {"type": "shell", "command": "true"},
        "approve": {
            "type": "approval",
            "needs": ["verify"],
            "summary_ref": "steps.verify.summary",
            "content_ref": "steps.verify.outputs.fingerprint",
        },
        "publish": {"type": "shell", "needs": ["approve"], "command": "true"},
    }
    package["sha256"] = "api-approval"
    store.register("example", package)
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    store.approve_runner("runner", ["example"], organization_id=owner["organization_id"])
    with TestClient(app) as client:
        login = browser_login(client, store)
        start = client.post(
            "/admin/runs",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={"project": "example", "workflow": "commands", "inputs": {}},
        )
        assert start.status_code == 200
        run_id = start.json()["run_id"]
        assignment = store.claim("runner", ["example"])
        store.complete(
            "runner",
            assignment["lease_id"],
            assignment["generation"],
            StepResult(status="succeeded", summary="Checks passed", outputs={"fingerprint": "b" * 64}),
        )
        rejected_csrf = client.post(
            f"/admin/runs/{run_id}/steps/approve/approval",
            headers={"X-CSRF-Token": "invalid"},
            json={"decision": "approved"},
        )
        assert rejected_csrf.status_code == 403
        approved = client.post(
            f"/admin/runs/{run_id}/steps/approve/approval",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={"decision": "approved", "note": "Reviewed"},
        )
        assert approved.status_code == 200
        assert approved.json()["outputs"]["content_fingerprint"] == "b" * 64


def test_notification_endpoint_is_owner_configured_and_never_returned(tmp_path, monkeypatch):
    app = application(tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    store = app.state.store
    with TestClient(app) as client:
        login = browser_login(client, store)
        headers = {"X-CSRF-Token": login.json()["csrf"]}
        saved = client.put(
            "/admin/notification-targets/webhook",
            headers=headers,
            json={"url": "https://hooks.example.test/private-token"},
        )
        assert saved.status_code == 200
        assert "private-token" not in saved.text
        state = client.get("/admin/state").json()
        assert state["notification_targets"] == {"webhook": True, "slack": False}
        disabled = client.put(
            "/admin/notification-events",
            headers=headers,
            json={"events": ["run.failed"]},
        )
        assert disabled.status_code == 200
        assert disabled.json()["events"]["run.succeeded"] is False
        deleted = client.delete("/admin/notification-targets/webhook", headers=headers)
        assert deleted.status_code == 200
