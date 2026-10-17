import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from aorch.config import snapshot
from aorch.contracts import StepResult
from aorch.engine.storage import StateConflict, Store

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/local-actions"


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "engine.sqlite")
    db.register("project", snapshot(EXAMPLE), now=0)
    owner = db.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    for runner in ("runner", "other", "a", "b", "old", "new"):
        db.approve_runner(runner, ["project"], organization_id=owner["organization_id"])
    return db


def finish(store, assignment, **changes):
    store.complete(
        "runner",
        assignment["lease_id"],
        assignment["generation"],
        StepResult(status="succeeded", summary="done", **changes),
    )


def test_atomic_claim(store):
    run = store.enqueue("project", "commands")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda name: store.claim(name, ["project"]), ["a", "b"]))
    assert sum(claim is not None for claim in claims) == 1
    assert store.get_run(run)["status"] == "running"


def test_inline_prompt_requires_protocol_five_runner(store, tmp_path):
    config = {
        "version": 1,
        "name": "inline-prompt",
        "profiles": {
            "reviewer": {"adapter": "codex", "authentication": "local"}
        },
        "workflows": {
            "review": {
                "steps": {
                    "inspect": {
                        "type": "agent",
                        "profile": "reviewer",
                        "prompt_text": "Review the change.",
                    }
                }
            }
        },
    }
    source = tmp_path / "inline-config.yaml"
    source.write_text(yaml.safe_dump(config))
    store.register("project", snapshot(source))
    store.enqueue("project", "review")

    assert store.claim("runner", ["project"], protocol_version="4") is None
    assert store.claim("runner", ["project"], protocol_version="5") is not None


def test_runner_project_access_can_be_updated_without_reissuing_credentials(store):
    owner = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    store.set_runner_projects("runner", ["project"], owner["organization_id"])
    with store.read_transaction() as db:
        projects = db.execute("SELECT projects FROM runners WHERE id='runner'").fetchone()[0]
    assert json.loads(projects) == ["project"]
    store.set_runner_projects("runner", [], owner["organization_id"])
    with store.read_transaction() as db:
        projects = db.execute("SELECT projects FROM runners WHERE id='runner'").fetchone()[0]
    assert json.loads(projects) == []
    with pytest.raises(StateConflict, match="Runner not found"):
        store.set_runner_projects("runner", ["project"], "another-organization")


def test_project_environment_secrets_are_encrypted_and_claim_scoped(store, tmp_path):
    store.secrets_key = b"k" * 32
    store.set_project_environment("project", "PUBLIC_ENDPOINT", "https://example.test")
    store.set_project_environment("project", "DEPLOY_TOKEN", "very-secret-token", secret=True)
    raw = (tmp_path / "engine.sqlite").read_bytes()
    assert b"very-secret-token" not in raw
    assert store.list_project_environment("project") == [
        {"name": "DEPLOY_TOKEN", "secret": True, "value": None},
        {"name": "PUBLIC_ENDPOINT", "secret": False, "value": None},
    ]
    store.enqueue("project", "commands")
    assignment = store.claim("runner", ["project"])
    assert assignment["project_environment"] == {
        "DEPLOY_TOKEN": "very-secret-token",
        "PUBLIC_ENDPOINT": "https://example.test",
    }
    assert assignment["project_secret_names"] == ["DEPLOY_TOKEN"]


def test_overview_does_not_block_runner_heartbeats(store):
    def poll_overview(_):
        for _ in range(30):
            store.overview()

    def send_heartbeats(_):
        for _ in range(30):
            store.heartbeat("runner", {"test": True})

    with ThreadPoolExecutor(max_workers=2) as pool:
        overview = pool.submit(poll_overview, None)
        heartbeats = pool.submit(send_heartbeats, None)
        overview.result()
        heartbeats.result()


def test_new_action_contract_requires_capable_runner_and_valid_result(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "version: 3\nname: contracts\nworkflows:\n  publish:\n    steps:\n"
        "      branch:\n        type: action\n        action: git.branch\n"
    )
    database = Store(tmp_path / "contracts.sqlite")
    package = snapshot(config)
    database.register("project", package, now=0)
    owner = database.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")
    database.approve_runner("old", ["project"], organization_id=owner["organization_id"])
    database.approve_runner("new", ["project"], organization_id=owner["organization_id"])
    database.enqueue("project", "publish")
    assert database.claim(
        "old", ["project"], adapters=["action"], action_contracts={}, now=1
    ) is None
    assignment = database.claim(
        "new",
        ["project"],
        adapters=["action"],
        action_contracts={"git.branch": 2},
        now=1,
    )
    assert assignment["workflow_name"] == "publish"
    with pytest.raises(StateConflict, match="Invalid git.branch v2 result"):
        database.complete(
            "new",
            assignment["lease_id"],
            assignment["generation"],
            StepResult(status="succeeded", summary="missing outputs"),
            now=2,
        )
    database.complete(
        "new",
        assignment["lease_id"],
        assignment["generation"],
        StepResult(
            status="succeeded",
            summary="branch ready",
            outputs={
                "repository": "https://example.test/repo.git",
                "branch": "codex/run",
                "starting_commit": "a" * 40,
                "mode": "checkout",
                "owner": "run",
            },
        ),
        now=2,
    )
    assert database.get_run(assignment["run_id"])["status"] == "succeeded"


def test_sequential_items_and_restart(store):
    runs = store.ingest("project", "commands", "jira", [{"id": "ONE"}, {"id": "TWO"}])
    assert len(runs) == 2
    assert store.ingest("project", "commands", "jira", [{"id": "ONE"}]) == []
    first = store.claim("runner", ["project"])
    assert store.claim("other", ["project"]) is None
    finish(store, first)
    store = Store(store.path)
    second = store.claim("runner", ["project"])
    assert second["run_id"] == first["run_id"]
    assert second["step_name"] == "second"
    finish(store, second)
    assert store.get_run(first["run_id"])["status"] == "succeeded"
    assert store.claim("runner", ["project"])["run_id"] != first["run_id"]


def test_result_idempotence_and_ownership(store):
    store.enqueue("project", "commands")
    claim = store.claim("runner", ["project"])
    with pytest.raises(StateConflict):
        store.complete(
            "intruder",
            claim["lease_id"],
            1,
            StepResult(status="succeeded", summary="done"),
        )
    with pytest.raises(StateConflict):
        store.complete(
            "runner",
            claim["lease_id"],
            2,
            StepResult(status="succeeded", summary="done"),
        )
    finish(store, claim)
    count = len(store.events())
    finish(store, claim)
    assert len(store.events()) == count
    with pytest.raises(StateConflict):
        store.complete(
            "runner",
            claim["lease_id"],
            1,
            StepResult(status="failed", summary="different"),
        )


def test_expiry_blocks_reassignment(store):
    run = store.enqueue("project", "commands")
    claim = store.claim("runner", ["project"], now=0)
    store.expire(now=61)
    assert store.get_run(run)["status"] == "reconciling"
    assert store.claim("other", ["project"], now=62) is None
    with pytest.raises(StateConflict):
        finish(store, claim)
    with pytest.raises(StateConflict):
        store.renew("runner", claim["lease_id"], 1, now=62)


def test_periodic_coalescing(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["interval_seconds"] = 300
    package["sha256"] = "periodic"
    store.register("periodic", package, now=0)
    with store.transaction() as db:
        organization_id = db.execute("SELECT organization_id FROM project_organizations LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO project_organizations VALUES(?,?,?)", ("periodic", organization_id, "periodic"))
    assert len(store.tick(now=10000)) == 1
    assert store.tick(now=10001) == []
    assert Store(store.path).tick(now=20000) == []


def test_version_two_periodic_starts_after_interval(store):
    package = snapshot(EXAMPLE)
    workflow = package["configuration"]["workflows"]["commands"]
    package["configuration"]["version"] = 2
    workflow["interval_seconds"] = None
    workflow["triggers"] = [{"type": "periodic", "interval_seconds": 60}]
    package["sha256"] = "version-two-periodic"
    store.register("periodic-v2", package, now=0)
    with store.transaction() as db:
        organization_id = db.execute("SELECT organization_id FROM project_organizations LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO project_organizations VALUES(?,?,?)", ("periodic-v2", organization_id, "periodic-v2"))
    assert store.tick(now=59) == []
    assert len(store.tick(now=60)) == 1


def test_delete_terminal_run_removes_history_and_artifacts(store, tmp_path):
    run = store.enqueue("project", "commands")
    while store.get_run(run)["status"] in ("queued", "running"):
        claim = store.claim("runner", ["project"])
        finish(store, claim)
    artifact_path = "test-artifact"
    store.artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = store.artifact_root / artifact_path
    artifact.write_text("artifact")
    with store.transaction() as db:
        attempt = db.execute("SELECT id FROM attempts WHERE run=? LIMIT 1", (run,)).fetchone()[0]
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)", ("test-id", attempt, artifact_path, "digest", 8))
    assert store.delete_run(run) == {"deleted": run}
    assert not artifact.exists()
    assert not any(event["run"] == run for event in store.events())
    with pytest.raises(StateConflict, match="Unknown run"):
        store.get_run(run)


def test_cannot_delete_active_run(store):
    run = store.enqueue("project", "commands")
    with pytest.raises(StateConflict, match="Cancel and stop"):
        store.delete_run(run)


def test_delete_project_clears_history_and_artifacts_but_keeps_workspaces(store):
    store.set_project("project", "local", str(EXAMPLE), path=str(EXAMPLE))
    run = store.enqueue("project", "commands")
    while store.get_run(run)["status"] in ("queued", "running"):
        finish(store, store.claim("runner", ["project"]))

    artifact_path = "project-report"
    store.artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = store.artifact_root / artifact_path
    artifact.write_text("report")
    with store.transaction() as db:
        attempt = db.execute(
            "SELECT id FROM attempts WHERE run=? LIMIT 1", (run,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?,?)",
            ("project-report", attempt, artifact_path, "digest", 6),
        )

    workspace = store.path.parent / "workspaces" / run
    workspace.mkdir(parents=True)
    assert store.delete_project("project") == {"deleted": "project"}
    assert not artifact.exists()
    assert workspace.is_dir()
    assert not store.overview()["projects"]
    assert not store.overview()["workflows"]
    assert not store.overview()["runs"]
    with pytest.raises(StateConflict, match="Unknown project"):
        store.delete_project("project")


def test_cannot_delete_project_with_active_runs(store):
    store.set_project("project", "local", str(EXAMPLE), path=str(EXAMPLE))
    store.enqueue("project", "commands")
    with pytest.raises(StateConflict, match="active runs"):
        store.delete_project("project")


def test_empty_discovery_and_needs_input_retry(store):
    assert store.ingest("project", "commands", "jira", []) == []
    runs = store.ingest(
        "project", "commands", "jira", [{"id": "ONE", "description": "old"}]
    )
    claim = store.claim("runner", ["project"])
    store.complete(
        "runner",
        claim["lease_id"],
        1,
        StepResult(status="needs_input", summary="question"),
    )
    assert store.get_run(runs[0])["status"] == "needs_input"
    assert (
        len(
            store.ingest(
                "project", "commands", "jira", [{"id": "ONE", "description": "new"}]
            )
        )
        == 1
    )
    assert "old" in store.get_run(runs[0])["item_snapshot"]


def test_permissions(store):
    store.enqueue("project", "commands")
    assert store.claim("runner", []) is None


def test_repeat_limit(store):
    package = snapshot(EXAMPLE)
    workflow = package["configuration"]["workflows"]["commands"]
    workflow["steps"] = {"first": workflow["steps"]["first"]}
    workflow["steps"]["first"]["repeat"] = {
        "max_iterations": 2,
        "until": "steps.first.outputs.verified == True",
    }
    package["sha256"] = "repeat"
    store.register("repeat", package)
    with store.transaction() as db:
        organization_id = db.execute("SELECT organization_id FROM project_organizations LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO project_organizations VALUES(?,?,?)", ("repeat", organization_id, "repeat"))
    store.approve_runner("runner", ["project", "repeat"], organization_id=organization_id)
    run = store.enqueue("repeat", "commands")
    for generation in (1, 2):
        claim = store.claim("runner", ["repeat"])
        assert claim["generation"] == generation
        finish(store, claim, outputs={"verified": False})
    assert store.get_run(run)["status"] == "needs_attention"


def test_snapshot_revision_pinned(store):
    run = store.enqueue("project", "commands")
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["steps"]["first"]["argv"] = [
        "echo",
        "changed",
    ]
    package["sha256"] = "new"
    store.register("project", package)
    claim = store.claim("runner", ["project"])
    assert claim["run_id"] == run
    assert claim["step"]["argv"][0] == "python3"


@pytest.mark.parametrize("items", [[], [{"id": "A"}, {"id": "B"}]])
def test_discovery_completion_is_atomic(store, items):
    package = snapshot(EXAMPLE)
    workflow = package["configuration"]["workflows"]["commands"]
    package["configuration"]["workflows"]["discover"] = {
        "steps": {"query": {"type": "action", "argv": ["jira-query"]}},
        "discovery": {"source": "jira", "target_workflow": "commands"},
    }
    package["sha256"] = "discovery"
    store.register("project", package)
    run = store.enqueue("project", "discover")
    claim = store.claim("runner", ["project"])
    finish(store, claim, outputs={"work_items": items})
    assert store.get_run(run)["status"] == "succeeded"
    with store.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM runs WHERE workflow='commands'"
        ).fetchone()[0] == len(items)
    finish(store, claim, outputs={"work_items": items})
    assert workflow["max_active_runs"] == 1


def test_invalid_discovery_rolls_back_completion(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["discover"] = {
        "steps": {"query": {"type": "action", "argv": ["query"]}},
        "discovery": {"source": "jira", "target_workflow": "commands"},
    }
    package["sha256"] = "discovery"
    store.register("project", package)
    run = store.enqueue("project", "discover")
    claim = store.claim("runner", ["project"])
    with pytest.raises(StateConflict):
        finish(store, claim, outputs={"work_items": [{"id": "valid"}, {}]})
    assert store.get_run(run)["status"] == "running"
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_parallel_failure_keeps_item_reserved(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["steps"]["second"]["needs"] = []
    package["configuration"]["workflows"]["commands"]["steps"]["second"]["when"] = None
    package["sha256"] = "parallel"
    store.register("project", package)
    run = store.ingest("project", "commands", "jira", [{"id": "ONE"}])[0]
    first = store.claim("runner", ["project"], capacity=2)
    assert store.claim("runner", ["project"], capacity=2) is not None
    store.complete(
        "runner", first["lease_id"], 1, StepResult(status="failed", summary="failed")
    )
    assert store.get_run(run)["status"] == "reconciling"
    assert store.ingest("project", "commands", "jira", [{"id": "ONE"}]) == []


def test_failure_handlers_run_after_restart_and_independently(store):
    package = snapshot(EXAMPLE)
    workflow = package["configuration"]["workflows"]["commands"]
    workflow["steps"]["second"]["needs"] = ["first"]
    workflow["on_failure"] = {
        "report": {
            "type": "action",
            "argv": ["python3", "-c", "pass"],
            "input_refs": {"reason": "failure.summary"},
        },
        "transition": {
            "type": "action",
            "argv": ["python3", "-c", "pass"],
            "input_refs": {"issue": "inputs.issue"},
        },
    }
    package["sha256"] = "failure-handlers"
    store.register("project", package)
    run = store.enqueue("project", "commands", {"issue": "TEST-1"})
    assignment = store.claim("runner", ["project"])
    store.complete(
        "runner",
        assignment["lease_id"],
        assignment["generation"],
        StepResult(status="failed", summary="Unit tests failed", error="TestFailure"),
    )

    store = Store(store.path)
    report = store.claim("runner", ["project"])
    assert report is not None, store.get_run(run)
    assert report["step_name"] == "failure__report"
    assert report["failure"]["summary"] == "Unit tests failed"
    store.complete(
        "runner", report["lease_id"], report["generation"],
        StepResult(status="failed", summary="Jira comment unavailable", error="ConnectError"),
    )
    transition = store.claim("runner", ["project"])
    assert transition["step_name"] == "failure__transition"
    assert transition["inputs"] == {"issue": "TEST-1"}
    store.complete(
        "runner", transition["lease_id"], transition["generation"],
        StepResult(status="succeeded", summary="Moved to Open Question"),
    )
    result = store.get_run(run)
    assert result["status"] == "needs_attention"
    assert next(step for step in result["steps"] if step["name"] == "second")["status"] == "skipped"


def test_failure_handler_does_not_run_for_success(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["on_failure"] = {
        "report": {"type": "action", "argv": ["python3", "-c", "pass"]}
    }
    package["sha256"] = "failure-handler-success"
    store.register("project", package)
    run = store.enqueue("project", "commands")
    assignment = store.claim("runner", ["project"])
    finish(store, assignment)
    assignment = store.claim("runner", ["project"])
    finish(store, assignment)
    result = store.get_run(run)
    assert result["status"] == "succeeded"
    assert next(step for step in result["steps"] if step["name"] == "failure__report")["status"] == "skipped"


def test_notification_outbox_tracks_each_state_change_and_stops_retrying(store):
    run = store.enqueue("project", "commands")
    with store.transaction() as db:
        store._event(db, run, "run.failed")
        store._event(db, run, "run.failed")
    notices = [item for item in store.notification_status() if item["run"] == run]
    assert len(notices) == 2
    assert all(item["delivered"] is None for item in notices)
    assert all("agent" not in item["event"] for item in notices)
    first = notices[0]
    for attempt in range(8):
        store.notification_result(first["id"], "ConnectError", now=1000 + attempt * 3600)
    failed = next(item for item in store.notification_status() if item["id"] == first["id"])
    assert failed["attempts"] == 8
    assert failed["delivered"] is not None
    assert failed["error"] == "ConnectError"


def test_notification_targets_are_encrypted_and_scoped_to_organization(store, tmp_path):
    organization_id = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")["organization_id"]
    store.secrets_key = b"n" * 32
    endpoint = "https://hooks.example.test/private-token"
    store.set_notification_target(organization_id, "webhook", endpoint)
    assert store.notification_targets_for(organization_id) == [("webhook", endpoint)]
    assert store.notification_targets_for("different-org") == []
    assert store.notification_target_status(organization_id) == {"webhook": True, "slack": False}
    assert endpoint.encode() not in (tmp_path / "engine.sqlite").read_bytes()
    assert store.delete_notification_target(organization_id, "webhook")
    assert store.notification_targets_for(organization_id) == []


def test_notification_preferences_filter_outbox_events(store):
    organization_id = store.bootstrap_owner("owner@example.test", "owner", "Owner", "owner-org")["organization_id"]
    store.set_notification_event_preferences(organization_id, ["run.failed"])
    run = store.enqueue("project", "commands")
    assignment = store.claim("runner", ["project"])
    store.complete(
        "runner", assignment["lease_id"], assignment["generation"],
        StepResult(status="succeeded", summary="done"),
    )
    events = [item["event"] for item in store.notification_status() if item["run"] == run]
    assert events == []
    assert store.notification_event_preferences(organization_id)["run.succeeded"] is False


def test_notification_outbox_prunes_only_terminal_old_records(store):
    run = store.enqueue("project", "commands")
    with store.transaction() as db:
        store._event(db, run, "run.failed")
        db.execute("UPDATE notification_outbox SET created=1,delivered=2 WHERE run=?", (run,))
        db.execute("INSERT INTO notification_outbox(id,run,event_id,event,payload,next_due,created,organization_id) VALUES('pending-old',?,999,'run.failed','{}',1,1,'org')", (run,))
    assert store.prune_notifications(now=40 * 86400) == 2
    with store.read_transaction() as db:
        assert db.execute("SELECT 1 FROM notification_outbox WHERE id='pending-old'").fetchone() is None


def test_human_answers_resume_once_and_leave_the_original_run_unchanged(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["steps"] = {
        "question": {"type": "action", "argv": ["python3", "-c", "print('question')"]}
    }
    package["sha256"] = "needs-input-answers"
    store.register("project", package)
    run = store.enqueue("project", "commands")
    claim = store.claim("runner", ["project"])
    store.complete(
        "runner",
        claim["lease_id"],
        claim["generation"],
        StepResult(
            status="needs_input",
            summary="Clarification needed",
            outputs={"ambiguities": [{"id": "scope", "question": "Which folder?"}]},
        ),
    )
    answers = {"scope": "src/"}
    resumed = store.answer_needs_input(run, answers)
    assert store.answer_needs_input(run, answers) == resumed
    with pytest.raises(StateConflict, match="different submitted answer"):
        store.answer_needs_input(run, {"scope": "docs/"})
    assert store.get_run(run)["status"] == "needs_input"
    assert json.loads(store.get_run(resumed)["inputs"])["answers"] == answers


def test_approval_requires_allowed_role_and_expires_without_a_runner(store):
    package = snapshot(EXAMPLE)
    package["configuration"]["workflows"]["commands"]["steps"] = {
        "verify": {"type": "shell", "command": "true"},
        "approve": {
            "type": "approval",
            "needs": ["verify"],
            "summary_ref": "steps.verify.summary",
            "content_ref": "steps.verify.outputs.fingerprint",
            "approval_timeout_seconds": 60,
        },
        "publish": {"type": "shell", "needs": ["approve"], "command": "true"},
    }
    package["sha256"] = "approval-workflow"
    store.register("project", package)
    run = store.enqueue("project", "commands")
    claim = store.claim("runner", ["project"])
    store.complete(
        "runner",
        claim["lease_id"],
        claim["generation"],
        StepResult(status="succeeded", summary="Tests passed", outputs={"fingerprint": "a" * 64}),
    )
    pending = store.get_run(run)
    assert pending["status"] == "waiting_approval"
    approval = next(step for step in pending["steps"] if step["name"] == "approve")
    assert approval["status"] == "waiting_approval"
    with pytest.raises(StateConflict, match="role cannot approve"):
        store.decide_approval(run, "approve", "member-id", "member", "approved")
    assert store.get_run(run)["status"] == "waiting_approval"
    expired_at = json.loads(approval["result"])["requested_at"] + 61
    assert store.expire_approvals(expired_at) == [run]
    assert store.get_run(run)["status"] == "needs_attention"
