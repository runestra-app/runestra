import asyncio
import json
from pathlib import Path

import httpx
import pytest

from aorch.config import snapshot
from aorch.config.service import EngineConfig, RunnerConfig
from aorch.contracts import StepResult
from aorch.engine.api import create_app
from aorch.engine.operations import backup, engine_lock
from aorch.engine.storage import Store
from aorch.runner.client import Runner
from aorch.runner.journal import Journal
from aorch.runner.package import materialize

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/local-actions"


def make_secret(path, value):
    path.write_text(value)
    path.chmod(0o600)
    return path


def test_lost_result_response_replays_once(tmp_path):
    app = create_app(
        EngineConfig(
            database=tmp_path / "engine.sqlite",
            artifacts=tmp_path / "artifacts",
            admin_token_file=make_secret(tmp_path / "admin", "a" * 40),
            session_key_file=make_secret(tmp_path / "session", "s" * 40),
        )
    )
    store = app.state.store
    store.register("project", snapshot(EXAMPLE))
    store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    token = store.approve_runner("runner", ["project"])
    run = store.enqueue("project", "commands")
    assignment = store.claim("runner", ["project"])
    result = StepResult(status="succeeded", summary="done")
    config = RunnerConfig(
        id="runner",
        token_file=make_secret(tmp_path / "token", token),
        journal=tmp_path / "journal.sqlite",
        workspace_root=tmp_path / "workspaces",
    )

    async def scenario():
        runner = Runner(config)
        await runner.client.aclose()
        runner.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )
        runner.journal.save(assignment)
        runner.journal.completed(
            assignment["attempt_id"], result.model_dump(mode="json")
        )
        store.complete(
            "runner", assignment["lease_id"], assignment["generation"], result
        )
        before = len(store.events())
        await runner.recover()
        assert not runner.journal.pending()
        assert len(store.events()) == before
        assert store.get_run(run)["steps"][0]["status"] == "succeeded"
        await runner.client.aclose()

    asyncio.run(scenario())


def test_safe_package_unpacking(tmp_path):
    package = snapshot(EXAMPLE)
    package["files"]["../escape"] = {"content": "bad", "sha256": "bad"}
    import hashlib

    package["sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in package.items() if key != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="path"):
        materialize(package, tmp_path / "package")
    assert not (tmp_path / "escape").exists()


def test_package_materialization_rejects_symlink_destination(tmp_path):
    package = snapshot(EXAMPLE)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "package"
    destination.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="destination"):
        materialize(package, destination)


def test_runner_journal_encrypts_project_secrets(tmp_path):
    token = "runner-token-value-long-enough-for-encryption-123"
    journal = Journal(tmp_path / "runner.sqlite", secrets_key=token)
    assignment = {
        "attempt_id": "attempt-1",
        "project_environment": {"DEPLOY_TOKEN": "journal-secret", "MODE": "release"},
        "project_secret_names": ["DEPLOY_TOKEN"],
    }
    journal.save(assignment)
    assert b"journal-secret" not in (tmp_path / "runner.sqlite").read_bytes()
    recovered = journal.pending()[0]
    assert json.loads(recovered["assignment"])["project_environment"] == assignment["project_environment"]


def test_resource_lock_across_workflows_and_restart(tmp_path):
    store = Store(tmp_path / "engine.sqlite")
    package = snapshot(EXAMPLE)
    for name in ("slow", "failure"):
        for step in package["configuration"]["workflows"][name]["steps"].values():
            step["resources"] = ["simulator"]
    package["sha256"] = "resources"
    store.register("project", package)
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    store.approve_runner("runner", ["project"], organization_id=owner["organization_id"])
    store.approve_runner("other", ["project"], organization_id=owner["organization_id"])
    first_run = store.enqueue("project", "slow")
    store.enqueue("project", "failure")
    first = store.claim("runner", ["project"], resources=["simulator"])
    assert first["run_id"] == first_run
    assert (
        Store(store.path).claim("other", ["project"], resources=["simulator"]) is None
    )
    store.complete(
        "runner",
        first["lease_id"],
        first["generation"],
        StepResult(status="succeeded", summary="done"),
    )
    assert store.claim("other", ["project"], resources=["simulator"]) is not None


def test_resource_uncertainty_keeps_lock(tmp_path):
    store = Store(tmp_path / "engine.sqlite")
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["slow"]["steps"]["wait"]["resources"] = [
        "device"
    ]
    package["sha256"] = "resource"
    store.register("project", package)
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    store.approve_runner("runner", ["project"], organization_id=owner["organization_id"])
    run = store.enqueue("project", "slow")
    first = store.claim("runner", ["project"])
    store.complete(
        "runner",
        first["lease_id"],
        1,
        StepResult(
            status="needs_attention",
            summary="Shutdown uncertain",
            error="ResourceUncertain",
        ),
    )
    assert store.get_run(run)["status"] == "reconciling"
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM resource_locks").fetchone()[0] == 1
    store.acknowledge_stop("runner", first["lease_id"], 1, uncertain=True)
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM resource_locks").fetchone()[0] == 0


def test_backup_and_single_engine_guard(tmp_path):
    store = Store(tmp_path / "engine.sqlite")
    store.register("project", snapshot(EXAMPLE))
    run = store.enqueue("project", "commands")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    backup(store, artifacts, tmp_path / "backup")
    restored = Store(tmp_path / "backup/engine.sqlite")
    assert restored.get_run(run)["status"] == "queued"


def test_notification_schema_migrates_from_previous_release(tmp_path):
    path = tmp_path / "engine.sqlite"
    Store(path)
    with Store(path).transaction() as db:
        db.execute("DROP TABLE notification_event_preferences")
        db.execute("DROP TABLE notification_targets")
        db.execute("ALTER TABLE notification_outbox DROP COLUMN organization_id")
        db.execute("PRAGMA user_version=15")

    migrated = Store(path)
    with migrated.read_transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17
        columns = {row["name"] for row in db.execute("PRAGMA table_info(notification_outbox)")}
        assert "organization_id" in columns
        assert db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('notification_targets','notification_event_preferences')"
        ).fetchone()[0] == 2


def test_resource_names_are_scoped_to_organization(tmp_path):
    store = Store(tmp_path / "engine.sqlite")
    package = snapshot(EXAMPLE)
    for workflow in package["configuration"]["workflows"].values():
        for step in workflow["steps"].values():
            step["resources"] = ["shared-device"]
    package["sha256"] = "multi-tenant-resources"
    store.register("project-one", package)
    store.register("project-two", package)
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    other_organization = "other-org-id"
    with store.transaction() as db:
        db.execute(
            "INSERT INTO organizations(id,slug,display_name,created_by,created) VALUES(?,?,?,?,?)",
            (other_organization, "other-org", "Other Org", owner["user_id"], 1),
        )
        db.execute(
            "UPDATE project_organizations SET organization_id=? WHERE project=?",
            (other_organization, "project-two"),
        )
    store.approve_runner("runner-one", ["project-one"], organization_id=owner["organization_id"])
    store.approve_runner("runner-two", ["project-two"], organization_id=other_organization)
    store.enqueue("project-one", "commands")
    store.enqueue("project-two", "commands")

    assert store.claim("runner-one", ["project-one"], resources=["shared-device"])
    assert store.claim("runner-two", ["project-two"], resources=["shared-device"])
    with (
        engine_lock(store.path),
        pytest.raises(ValueError, match="Another engine"),
        engine_lock(store.path),
    ):
        pass
