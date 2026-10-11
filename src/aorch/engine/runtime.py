"""Runner identities, recovery, events and artifact metadata."""

import hashlib
import json
import secrets
import sqlite3
import time

from aorch.contracts import PROTOCOL_VERSION
from aorch.engine.errors import StateConflict


class RuntimeStore:
    def migrate_runtime(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                for statement in [
                    "CREATE TABLE runners (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, projects TEXT NOT NULL, capacity INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, heartbeat REAL, capabilities TEXT NOT NULL DEFAULT '{}')",
                    "CREATE TABLE runner_events (attempt TEXT, sequence INTEGER, payload TEXT, PRIMARY KEY(attempt,sequence))",
                    "CREATE TABLE artifacts (id TEXT PRIMARY KEY, attempt TEXT REFERENCES attempts(id), path TEXT, sha256 TEXT, size INTEGER)",
                    "CREATE TABLE affinity (run TEXT PRIMARY KEY REFERENCES runs(id), runner TEXT NOT NULL)",
                    "CREATE TABLE resource_locks (project TEXT, resource TEXT, run TEXT REFERENCES runs(id), runner TEXT, PRIMARY KEY(project,resource))",
                    "CREATE TABLE effects (run TEXT, effect_key TEXT, status TEXT, provider_id TEXT, PRIMARY KEY(run,effect_key))",
                ]:
                    db.execute(statement)
                db.execute("PRAGMA user_version=2")
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(runners)")
            }
            if "instance_id" not in columns:
                db.execute("ALTER TABLE runners ADD COLUMN instance_id TEXT")

    def approve_runner(self, runner, projects, capacity=1, organization_id=None, name="", replace=True):
        if capacity < 1:
            raise ValueError("Capacity must be positive")
        projects = list(projects)
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            project_orgs = {
                row["project"]: row["organization_id"]
                for row in db.execute(
                    "SELECT project,organization_id FROM project_organizations WHERE project IN ({})".format(
                        ",".join("?" for _ in projects) or "NULL"
                    ),
                    projects,
                )
            }
            if any(project not in project_orgs for project in projects):
                if organization_id is not None or project_orgs:
                    raise ValueError("Runner projects must belong to the selected organization")
            elif project_orgs:
                assigned_organizations = set(project_orgs.values())
                if len(assigned_organizations) != 1:
                    raise ValueError("A runner can only serve one organization")
                assigned_organization = next(iter(assigned_organizations))
                if organization_id is not None and organization_id != assigned_organization:
                    raise ValueError("Runner projects must belong to the selected organization")
                organization_id = assigned_organization
            statement = (
                "INSERT INTO runners(id,token_hash,projects,capacity,organization_id,name) VALUES(?,?,?,?,?,?) "
                + ("ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash,projects=excluded.projects,capacity=excluded.capacity,organization_id=excluded.organization_id,name=excluded.name,revoked=0" if replace else "")
            )
            try:
                db.execute(statement, (
                    runner, hashlib.sha256(token.encode()).hexdigest(), json.dumps(projects),
                    capacity, organization_id, name,
                ))
            except sqlite3.IntegrityError as error:
                if not replace and "UNIQUE constraint failed: runners.id" in str(error):
                    raise StateConflict("Runner identity already exists") from error
                raise
        return token

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM runners WHERE token_hash=? AND revoked=0", (digest,)
            ).fetchone()
            if row is None:
                raise StateConflict("Invalid runner credentials")
            return dict(row)

    def revoke_runner(self, runner):
        with self.transaction() as db:
            db.execute("UPDATE runners SET revoked=1 WHERE id=?", (runner,))

    def heartbeat(self, runner, capabilities):
        with self.transaction() as db:
            instance_id = capabilities.get("instance_id")
            now = time.time()
            current = db.execute(
                "SELECT heartbeat,instance_id,revoked FROM runners WHERE id=?",
                (runner,),
            ).fetchone()
            if current is None or current["revoked"]:
                raise StateConflict("Runner is not registered or has been revoked")
            if (
                current["heartbeat"] is not None
                and now - current["heartbeat"] < 30
                and current["instance_id"]
                and current["instance_id"] != instance_id
            ):
                raise StateConflict(
                    f"Runner '{runner}' is already connected from another process. "
                    "Stop that process or register this process with a different runner ID."
                )
            db.execute(
                "UPDATE runners SET heartbeat=?,capabilities=?,instance_id=? WHERE id=?",
                (now, json.dumps(capabilities), instance_id, runner),
            )

    def runner_event(self, runner, lease, generation, sequence, payload):
        encoded = json.dumps(payload, sort_keys=True)
        if len(encoded) > 65536 or sequence < 1:
            raise StateConflict("Invalid event size or sequence")
        with self.transaction() as db:
            attempt = self._assignment(db, runner, lease, generation)
            if attempt["status"] != "running" or attempt["expires"] <= time.time():
                raise StateConflict("Event assignment is not live")
            previous = db.execute(
                "SELECT payload FROM runner_events WHERE attempt=? AND sequence=?",
                (attempt["id"], sequence),
            ).fetchone()
            if previous:
                if previous[0] != encoded:
                    raise StateConflict("Conflicting event replay")
                return
            db.execute(
                "INSERT INTO runner_events VALUES(?,?,?)",
                (attempt["id"], sequence, encoded),
            )
            self._event(
                db,
                attempt["run"],
                "runner.event",
                {"attempt": attempt["id"], "sequence": sequence, "data": payload},
            )

    def cancel(self, run):
        with self.transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run,)).fetchone()
            if row is None:
                raise StateConflict("Unknown run")
            if row[0] not in ("queued", "running", "reconciling"):
                return
            active = db.execute(
                "SELECT 1 FROM attempts WHERE run=? AND status IN ('running','reconciling')",
                (run,),
            ).fetchone()
            db.execute(
                "UPDATE runs SET status=? WHERE id=?",
                ("reconciling" if active else "cancelled", run),
            )
            self._event(db, run, "run.cancel_requested")

    def acknowledge_stop(
        self, runner, lease, generation, uncertain=False, stopped=True
    ):
        """Confirm process exit without pretending an external effect was undone."""
        with self.transaction() as db:
            attempt = self._assignment(db, runner, lease, generation)
            if attempt["status"] not in ("running", "reconciling"):
                return
            run = attempt["run"]
            if not stopped:
                db.execute(
                    "UPDATE attempts SET status='reconciling' WHERE id=?",
                    (attempt["id"],),
                )
                db.execute("UPDATE runs SET status='reconciling' WHERE id=?", (run,))
                self._event(
                    db, run, "attempt.stop_unconfirmed", {"attempt": attempt["id"]}
                )
                return
            status = "needs_attention" if uncertain else "cancelled"
            db.execute(
                "UPDATE attempts SET status=? WHERE id=?", (status, attempt["id"])
            )
            db.execute(
                "UPDATE steps SET status=? WHERE run=? AND name=?",
                (status, run, attempt["step"]),
            )
            remaining = db.execute(
                "SELECT 1 FROM attempts WHERE run=? AND status IN ('running','reconciling')",
                (run,),
            ).fetchone()
            if not remaining:
                terminal = db.execute(
                    "SELECT status FROM steps WHERE run=? AND status IN ('needs_attention','failed','needs_input') LIMIT 1",
                    (run,),
                ).fetchone()
                db.execute(
                    "UPDATE runs SET status=? WHERE id=?",
                    (terminal[0] if terminal else status, run),
                )
                db.execute("DELETE FROM resource_locks WHERE run=?", (run,))
            self._event(
                db,
                run,
                "attempt.stopped",
                {"attempt": attempt["id"], "uncertain": uncertain},
            )

    def renew_assignment(self, runner, lease, generation, lease_seconds, now=None):
        import time

        now = time.time() if now is None else now
        with self.transaction() as db:
            self._expire(db, now)
            attempt = self._assignment(db, runner, lease, generation)
            run = db.execute(
                "SELECT status FROM runs WHERE id=?", (attempt["run"],)
            ).fetchone()
            if attempt["status"] != "running" or run[0] != "running":
                raise StateConflict("Assignment is no longer running")
            db.execute(
                "UPDATE attempts SET expires=? WHERE id=?",
                (now + lease_seconds, attempt["id"]),
            )
            return now + lease_seconds

    def assignment_live(self, runner, lease, generation):
        with self.transaction() as db:
            attempt = self._assignment(db, runner, lease, generation)
            run = db.execute(
                "SELECT status FROM runs WHERE id=?", (attempt["run"],)
            ).fetchone()[0]
            return (
                attempt["status"] == "running"
                and attempt["expires"] > time.time()
                and run == "running"
            )

    def overview(self):
        with self.read_transaction() as db:
            result = {
                table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
                for table in (
                    "projects",
                    "workflows",
                    "runs",
                    "items",
                    "occurrences",
                    "runners",
                    "resource_locks",
                )
            }
            registered = {project["name"] for project in result["projects"]}
            for row in result["workflows"]:
                if row["project"] not in registered:
                    result["projects"].append({
                        "name": row["project"],
                        "source_type": "snapshot",
                        "source": "Persisted workflow snapshots",
                        "ref": None,
                        "path": None,
                        "commit_sha": None,
                        "status": "unconfigured",
                        "error": "This project has persisted workflow history but no registered source.",
                        "refreshed": None,
                    })
                    registered.add(row["project"])
            now = time.time()
            for runner in result["runners"]:
                runner.pop("token_hash")
                runner["online"] = bool(
                    runner["heartbeat"]
                    and now - runner["heartbeat"] < 30
                    and not runner["revoked"]
                )
            online = [runner for runner in result["runners"] if runner["online"]]
            for workflow_row in result["workflows"]:
                retention = db.execute(
                    "SELECT retention_count FROM workflow_retention WHERE project=? AND workflow=?",
                    (workflow_row["project"], workflow_row["name"]),
                ).fetchone()
                workflow_row["retention_count"] = retention[0] if retention else 30
                revision = db.execute(
                    "SELECT snapshot FROM revisions WHERE id=?", (workflow_row["revision"],)
                ).fetchone()
                config = json.loads(revision["snapshot"])["configuration"]
                definition = config["workflows"][workflow_row["name"]]
                triggers = definition.get("triggers", [])
                trigger = next((item for item in triggers if item.get("enabled", True)), None)
                interval = next(
                    (item["interval_seconds"] for item in triggers if item.get("type") == "periodic" and item.get("enabled", True)),
                    definition.get("interval_seconds"),
                )
                workflow_row["trigger"] = trigger or (
                    {"type": "periodic", "interval_seconds": interval} if interval else None
                )
                workflow_row["input_schema"] = definition.get("input_schema", {"type": "object"})
            for run in result["runs"]:
                run["blocked_reason"] = None
                if run["status"] != "queued":
                    continue
                row = db.execute(
                    "SELECT snapshot FROM revisions WHERE id=?", (run["revision"],)
                ).fetchone()
                package = json.loads(row["snapshot"])
                configuration = package["configuration"]
                workflow = configuration["workflows"][run["workflow"]]
                required_contracts = package.get("action_contracts", {}).get(
                    run["workflow"], {}
                )
                requires_protocol_five = any(
                    step.get("type") == "agent" and step.get("prompt_text") is not None
                    for step in workflow["steps"].values()
                )
                required = set()
                for step in workflow["steps"].values():
                    if step["type"] == "approval":
                        continue
                    if step["type"] == "action":
                        required.add("action")
                    elif step["type"] == "agent":
                        required.add(
                            configuration["profiles"][step["profile"]]["adapter"]
                        )
                    else:
                        required.add(step["type"])
                candidates = [
                    runner
                    for runner in online
                    if run["project"] in json.loads(runner["projects"])
                ]
                if not candidates:
                    run["blocked_reason"] = (
                        "No online approved runner can access this project"
                    )
                else:
                    capable = [
                        json.loads(runner["capabilities"])
                        for runner in candidates
                    ]
                    with_adapters = [
                        item
                        for item in capable
                        if required.issubset(set(item.get("adapters", [])))
                    ]
                    compatible = [
                        item
                        for item in with_adapters
                        if not requires_protocol_five
                        or item.get("protocol_version") == PROTOCOL_VERSION
                    ]
                    if not with_adapters:
                        run["blocked_reason"] = (
                            "Online runners lack required adapters: "
                            + ", ".join(sorted(required))
                        )
                    elif requires_protocol_five and not compatible:
                        run["blocked_reason"] = (
                            "Inline agent prompts require a protocol 5 runner"
                        )
                    elif not any(
                        all(
                            item.get("action_contracts", {}).get(name, 0) >= version
                            for name, version in required_contracts.items()
                        )
                        for item in compatible
                    ):
                        run["blocked_reason"] = (
                            "Online runners lack required action contracts: "
                            + ", ".join(
                                f"{name} v{version}"
                                for name, version in sorted(required_contracts.items())
                            )
                        )
            return result

    def assignment_live_without_transaction(self, db, attempt):
        run = db.execute(
            "SELECT status FROM runs WHERE id=?", (attempt["run"],)
        ).fetchone()[0]
        return (
            attempt["status"] == "running"
            and attempt["expires"] > time.time()
            and run == "running"
        )

    def resolve(self, run, status, processes_stopped, effects_reviewed, note):
        if (
            status not in ("cancelled", "needs_attention", "failed")
            or not processes_stopped
            or not effects_reviewed
            or not note.strip()
        ):
            raise StateConflict(
                "Resolution requires process-stop and effect-review confirmations plus a note"
            )
        with self.transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run,)).fetchone()
            if row is None or row[0] not in ("reconciling", "needs_attention"):
                raise StateConflict("Run does not require resolution")
            db.execute(
                "UPDATE attempts SET status=? WHERE run=? AND status IN ('running','reconciling')",
                (status, run),
            )
            db.execute(
                "UPDATE steps SET status=? WHERE run=? AND status IN ('running','ready','pending')",
                (status, run),
            )
            db.execute("UPDATE runs SET status=? WHERE id=?", (status, run))
            db.execute("DELETE FROM resource_locks WHERE run=?", (run,))
            self._event(db, run, "run.resolved", {"status": status, "note": note})
