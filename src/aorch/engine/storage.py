"""Transactional SQLite queue and durable scheduler state."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jsonschema import validate

from aorch.config.conditions import evaluate_condition
from aorch.config.models import ProjectConfig
from aorch.contracts import PROTOCOL_VERSION, StepResult

ACTIVE = ("queued", "running", "reconciling", "waiting_approval")
NOTIFICATION_EVENTS = (
    "run.failed", "run.needs_input", "approval.requested", "run.needs_attention",
    "run.reconciling", "run.succeeded",
)


from aorch.engine.errors import StateConflict
from aorch.engine.runtime import RuntimeStore


class Store(RuntimeStore):
    def __init__(self, path: Path, artifact_root: Path | None = None, secrets_key: str | None = None):
        self.path = path
        self.artifact_root = artifact_root or path.parent / "artifacts"
        self.secrets_key = (
            hmac.digest(secrets_key.encode(), b"Runestra project environment v1", "sha256")
            if secrets_key else None
        )
        # Set journal mode once when opening the store. Changing it on every
        # transaction can contend with concurrent runner heartbeats.
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
        finally:
            db.close()
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 18:
                raise StateConflict("Database version is newer than this engine")
            if version == 0:
                statements = [
                    "CREATE TABLE revisions (id TEXT PRIMARY KEY, project TEXT NOT NULL, snapshot TEXT NOT NULL)",
                    "CREATE TABLE workflows (project TEXT, name TEXT, revision TEXT NOT NULL REFERENCES revisions(id), next_due REAL, PRIMARY KEY(project,name))",
                    "CREATE TABLE occurrences (id TEXT PRIMARY KEY, project TEXT, workflow TEXT, kind TEXT, created REAL)",
                    "CREATE TABLE items (id TEXT PRIMARY KEY, project TEXT, workflow TEXT, source TEXT, external_id TEXT, snapshot TEXT, UNIQUE(project,workflow,source,external_id))",
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, project TEXT, workflow TEXT, revision TEXT REFERENCES revisions(id), occurrence TEXT REFERENCES occurrences(id), item TEXT REFERENCES items(id), item_snapshot TEXT, inputs TEXT NOT NULL, status TEXT NOT NULL, created REAL)",
                    "CREATE UNIQUE INDEX one_active_item ON runs(item) WHERE item IS NOT NULL AND status IN ('queued','running','reconciling')",
                    "CREATE TABLE steps (run TEXT REFERENCES runs(id), name TEXT, status TEXT, iteration INTEGER NOT NULL DEFAULT 0, result TEXT, PRIMARY KEY(run,name))",
                    "CREATE TABLE attempts (id TEXT PRIMARY KEY, run TEXT, step TEXT, runner TEXT, lease TEXT UNIQUE, generation INTEGER, expires REAL, status TEXT, result TEXT, FOREIGN KEY(run,step) REFERENCES steps(run,name))",
                    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT, kind TEXT, payload TEXT, created REAL)",
                ]
                for statement in statements:
                    db.execute(statement)
                db.execute("PRAGMA user_version=1")
        self.migrate_runtime()
        self.migrate_projects()
        self.migrate_retention()
        self.migrate_accounts()
        self.migrate_project_environment()
        self.migrate_approval_runs()
        self.migrate_answer_resumes()
        self.migrate_notifications()
        self.migrate_webhook_deliveries()
        self.migrate_notification_targets()
        self.migrate_notification_preferences()
        self.migrate_run_timing()

    def migrate_run_timing(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 18:
                db.execute("ALTER TABLE runs ADD COLUMN finished REAL")
                db.execute("CREATE TABLE run_status_history(id INTEGER PRIMARY KEY AUTOINCREMENT,run TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)")
                db.execute("CREATE INDEX run_status_history_run ON run_status_history(run,created,id)")
                rows = db.execute("SELECT id,status,created FROM runs ORDER BY created,id").fetchall()
                for row in rows:
                    history = [(row["created"], "queued")]
                    events = db.execute("SELECT kind,payload,created FROM events WHERE run=? ORDER BY id", (row["id"],)).fetchall()
                    for event in events:
                        status = {
                            "run.queued": "queued",
                            "run.running": "running",
                            "step.claimed": "running",
                            "run.reconciling": "reconciling",
                            "approval.requested": "waiting_approval",
                            "run.succeeded": "succeeded",
                            "run.failed": "failed",
                            "condition.failed": "failed",
                            "approval.invalid": "failed",
                            "run.cancelled": "cancelled",
                            "run.needs_input": "needs_input",
                            "run.needs_attention": "needs_attention",
                            "approval.expired": "needs_attention",
                        }.get(event["kind"])
                        if event["kind"] == "run.resolved":
                            try:
                                status = json.loads(event["payload"] or "{}").get("status")
                            except (TypeError, ValueError):
                                status = None
                        elif event["kind"] == "attempt.stopped":
                            status = row["status"] if row["status"] in ("cancelled", "needs_attention") else None
                        if status:
                            history.append((event["created"], status))
                    db.executemany("INSERT INTO run_status_history(run,status,created) VALUES(?,?,?)", [(row["id"], status, created) for created, status in history])
                    terminal = [created for created, status in history if status in ("succeeded", "failed", "cancelled", "needs_input", "needs_attention")]
                    if row["status"] in ("succeeded", "failed", "cancelled", "needs_input", "needs_attention") and terminal:
                        db.execute("UPDATE runs SET finished=? WHERE id=?", (terminal[-1], row["id"]))
                db.execute("""CREATE TRIGGER run_status_history_update AFTER UPDATE OF status ON runs
                    WHEN OLD.status != NEW.status BEGIN
                      INSERT INTO run_status_history(run,status,created) VALUES(NEW.id,NEW.status,(julianday('now')-2440587.5)*86400.0);
                      UPDATE runs SET finished=CASE WHEN NEW.status IN ('succeeded','failed','cancelled','needs_input','needs_attention') THEN (julianday('now')-2440587.5)*86400.0 ELSE NULL END WHERE id=NEW.id;
                    END""")
                db.execute("PRAGMA user_version=18")

    def migrate_approval_runs(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 12:
                db.execute("DROP INDEX IF EXISTS one_active_item")
                db.execute(
                    "CREATE UNIQUE INDEX one_active_item ON runs(item) WHERE item IS NOT NULL AND status IN ('queued','running','reconciling','waiting_approval')"
                )
                db.execute("PRAGMA user_version=12")

    def migrate_answer_resumes(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 13:
                db.execute("CREATE TABLE run_answers(source_run TEXT PRIMARY KEY REFERENCES runs(id),answer_hash TEXT NOT NULL,new_run TEXT NOT NULL REFERENCES runs(id),created REAL NOT NULL)")
                db.execute("PRAGMA user_version=13")

    def migrate_notifications(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 14:
                db.execute("CREATE TABLE notification_outbox(id TEXT PRIMARY KEY,run TEXT NOT NULL,event_id INTEGER NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_due REAL NOT NULL,delivered REAL,error TEXT,created REAL NOT NULL,UNIQUE(event_id))")
                db.execute("CREATE INDEX notification_outbox_due ON notification_outbox(delivered,next_due)")
                db.execute("PRAGMA user_version=14")

    def migrate_webhook_deliveries(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 15:
                db.execute("CREATE TABLE webhook_deliveries(provider TEXT NOT NULL,delivery TEXT NOT NULL,body_hash TEXT NOT NULL,run TEXT NOT NULL REFERENCES runs(id),created REAL NOT NULL,PRIMARY KEY(provider,delivery))")
                db.execute("PRAGMA user_version=15")

    def migrate_notification_targets(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 16:
                db.execute("ALTER TABLE notification_outbox ADD COLUMN organization_id TEXT")
                db.execute("CREATE TABLE notification_targets(organization_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('webhook','slack')),url_encrypted TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(organization_id,kind))")
                db.execute("CREATE INDEX notification_targets_org ON notification_targets(organization_id)")
                db.execute("PRAGMA user_version=16")

    def migrate_notification_preferences(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 17:
                db.execute("CREATE TABLE notification_event_preferences(organization_id TEXT NOT NULL,event TEXT NOT NULL,enabled INTEGER NOT NULL,PRIMARY KEY(organization_id,event))")
                db.execute("PRAGMA user_version=17")

    def migrate_project_environment(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 11:
                db.execute(
                    "CREATE TABLE project_environment (project TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, secret INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,name))"
                )
                db.execute("PRAGMA user_version=11")

    def set_project_environment(self, project, name, value, secret=False):
        if not isinstance(value, str) or not value or len(value) > 65536:
            raise ValueError("Environment values must contain 1 to 65536 characters")
        if secret:
            if self.secrets_key is None:
                raise ValueError("Project secret encryption is not configured")
            nonce = os.urandom(12)
            encrypted = nonce + AESGCM(self.secrets_key).encrypt(
                nonce, value.encode(), f"{project}:{name}".encode()
            )
            value = encrypted.hex()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO project_environment(project,name,value,secret) VALUES(?,?,?,?) ON CONFLICT(project,name) DO UPDATE SET value=excluded.value,secret=excluded.secret",
                (project, name, value, int(secret)),
            )

    def list_project_environment(self, project, include_values=False):
        with self.read_transaction() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT name,value,secret FROM project_environment WHERE project=? ORDER BY name", (project,)
            )]
        result = []
        for row in rows:
            secret = bool(row["secret"])
            value = None
            if include_values:
                value = row["value"]
                if secret:
                    if self.secrets_key is None:
                        raise ValueError("Project secret encryption key is unavailable")
                    encrypted = bytes.fromhex(value)
                    value = AESGCM(self.secrets_key).decrypt(
                        encrypted[:12], encrypted[12:], f"{project}:{row['name']}".encode()
                    ).decode()
            result.append({"name": row["name"], "secret": secret, "value": value})
        return result

    def delete_project_environment(self, project, name):
        with self.transaction() as db:
            return db.execute(
                "DELETE FROM project_environment WHERE project=? AND name=?", (project, name)
            ).rowcount > 0

    @contextmanager
    def transaction(self):
        db = self._connect()
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read_transaction(self):
        db = self._connect()
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def migrate_projects(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 3:
                db.execute(
                    "CREATE TABLE projects (name TEXT PRIMARY KEY, source_type TEXT NOT NULL, source TEXT NOT NULL, ref TEXT, path TEXT, commit_sha TEXT, status TEXT NOT NULL, error TEXT, refreshed REAL)"
                )
                db.execute("PRAGMA user_version=3")

    def migrate_retention(self):
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 4:
                db.execute("ALTER TABLE runs ADD COLUMN keep_forever INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE runs ADD COLUMN retention_pending INTEGER NOT NULL DEFAULT 0")
                db.execute("CREATE TABLE workflow_retention (project TEXT NOT NULL, workflow TEXT NOT NULL, retention_count INTEGER NOT NULL DEFAULT 30, PRIMARY KEY(project,workflow))")
                db.execute("INSERT INTO workflow_retention(project,workflow) SELECT project,name FROM workflows")
                db.execute("CREATE TABLE item_history (item TEXT NOT NULL, run TEXT NOT NULL, step TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(run,step))")
                db.execute("CREATE TABLE item_effects (item TEXT NOT NULL, effect_key TEXT NOT NULL, provider_id TEXT, PRIMARY KEY(item,effect_key))")
                db.execute("INSERT OR IGNORE INTO item_effects SELECT runs.item,effects.effect_key,effects.provider_id FROM effects JOIN runs ON runs.id=effects.run WHERE runs.item IS NOT NULL AND effects.status='done'")
                db.execute("INSERT OR IGNORE INTO item_history SELECT runs.item,runs.id,steps.name,steps.result,runs.created FROM steps JOIN runs ON runs.id=steps.run WHERE runs.item IS NOT NULL AND steps.result IS NOT NULL")
                db.execute("CREATE TABLE runner_log_policy (attempt TEXT PRIMARY KEY, runner TEXT NOT NULL, retained INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 1, acknowledged INTEGER NOT NULL DEFAULT 0)")
                db.execute("PRAGMA user_version=4")

    def migrate_accounts(self):
        """Add account, organization and authentication tables without changing legacy project IDs."""
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 5:
                for statement in [
                    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE, display_name TEXT NOT NULL, default_organization_id TEXT, created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE user_emails (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), email TEXT NOT NULL COLLATE NOCASE UNIQUE, verified REAL, is_primary INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE auth_identities (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), provider TEXT NOT NULL, subject TEXT NOT NULL, created REAL NOT NULL, UNIQUE(provider,subject))",
                    "CREATE TABLE organizations (id TEXT PRIMARY KEY, slug TEXT NOT NULL COLLATE NOCASE UNIQUE, display_name TEXT NOT NULL, created_by TEXT REFERENCES users(id), created REAL NOT NULL)",
                    "CREATE TABLE organization_memberships (organization_id TEXT NOT NULL REFERENCES organizations(id), user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL CHECK(role IN ('owner','admin','member')), created REAL NOT NULL, PRIMARY KEY(organization_id,user_id))",
                    "CREATE TABLE project_organizations (project TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id), slug TEXT NOT NULL COLLATE NOCASE, UNIQUE(organization_id,slug))",
                    "CREATE TABLE organization_invitations (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id), email TEXT NOT NULL COLLATE NOCASE, role TEXT NOT NULL CHECK(role IN ('admin','member')), token_hash TEXT NOT NULL UNIQUE, expires REAL NOT NULL, inviter_id TEXT NOT NULL REFERENCES users(id), accepted REAL, revoked REAL, created REAL NOT NULL)",
                    "CREATE TABLE browser_sessions (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL, created REAL NOT NULL, revoked REAL)",
                    "CREATE TABLE login_tokens (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, email TEXT NOT NULL COLLATE NOCASE, expires REAL NOT NULL, used REAL, created REAL NOT NULL)",
                    "CREATE TABLE oauth_transactions (state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, nonce TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL)",
                    "CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT REFERENCES users(id), organization_id TEXT REFERENCES organizations(id), action TEXT NOT NULL, target_id TEXT, created REAL NOT NULL)",
                    "CREATE INDEX memberships_user ON organization_memberships(user_id)",
                    "CREATE INDEX sessions_user ON browser_sessions(user_id,expires)",
                    "CREATE INDEX login_tokens_email ON login_tokens(email,expires)",
                ]:
                    db.execute(statement)
                db.execute("PRAGMA user_version=5")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 6:
                db.execute("ALTER TABLE runners ADD COLUMN organization_id TEXT REFERENCES organizations(id)")
                db.execute("PRAGMA user_version=6")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 7:
                db.execute("ALTER TABLE login_tokens ADD COLUMN requester_ip TEXT")
                db.execute("PRAGMA user_version=7")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 8:
                db.execute("CREATE TABLE runner_enrollments (token_hash TEXT PRIMARY KEY, runner_id TEXT NOT NULL UNIQUE, projects TEXT NOT NULL, capacity INTEGER NOT NULL, organization_id TEXT NOT NULL REFERENCES organizations(id), expires REAL NOT NULL, created REAL NOT NULL)")
                db.execute("PRAGMA user_version=8")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 9:
                db.execute("ALTER TABLE runners ADD COLUMN name TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE runner_enrollments ADD COLUMN name TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE runners SET name=id WHERE name=''")
                db.execute("PRAGMA user_version=9")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 10:
                db.execute(
                    "UPDATE resource_locks SET project=COALESCE((SELECT project_organizations.organization_id FROM runs JOIN project_organizations ON project_organizations.project=runs.project WHERE runs.id=resource_locks.run),'*') WHERE project='*'"
                )
                db.execute("PRAGMA user_version=10")

    def create_runner_enrollment(self, name, projects, capacity, organization_id):
        token = __import__("secrets").token_urlsafe(32)
        runner_id = __import__("secrets").token_urlsafe(24)
        now = time.time()
        with self.transaction() as db:
            db.execute("DELETE FROM runner_enrollments WHERE expires<=?", (now,))
            db.execute(
                "INSERT INTO runner_enrollments(token_hash,runner_id,projects,capacity,organization_id,expires,created,name) VALUES(?,?,?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), runner_id, json.dumps(projects), capacity, organization_id, now + 3600, now, name),
            )
        return runner_id, token

    def set_runner_projects(self, runner, projects, organization_id):
        with self.transaction() as db:
            row = db.execute(
                "SELECT organization_id FROM runners WHERE id=? AND revoked=0", (runner,)
            ).fetchone()
            if row is None or row["organization_id"] != organization_id:
                raise StateConflict("Runner not found")
            allowed = {
                item[0] for item in db.execute(
                    "SELECT project FROM project_organizations WHERE organization_id=?",
                    (organization_id,),
                )
            }
            if len(projects) != len(set(projects)) or not set(projects) <= allowed:
                raise ValueError("Runner projects must belong to this organization")
            db.execute("UPDATE runners SET projects=? WHERE id=?", (json.dumps(projects), runner))

    def consume_runner_enrollment(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runner_enrollments WHERE token_hash=? AND expires>?", (digest, now)).fetchone()
            if row is None:
                raise StateConflict("Runner setup key is invalid, expired, or already used")
            db.execute("DELETE FROM runner_enrollments WHERE token_hash=?", (digest,))
            enrollment = dict(row)
        credential = self.approve_runner(
            enrollment["runner_id"], json.loads(enrollment["projects"]),
            enrollment["capacity"], enrollment["organization_id"], enrollment["name"], replace=False,
        )
        return {"runner_id": enrollment["runner_id"], "token": credential}

    def local_auth_user(self):
        """Return the local owner, creating the first local account when needed."""
        with self.read_transaction() as db:
            user = db.execute(
                "SELECT users.id,users.username,users.display_name,users.default_organization_id FROM users JOIN organization_memberships m ON m.user_id=users.id WHERE m.role='owner' ORDER BY users.created LIMIT 1"
            ).fetchone()
        if user is None:
            with self.read_transaction() as db:
                has_users = db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            if has_users:
                raise StateConflict("Local sign-in requires an owner account. Bootstrap an owner first.")
            self.bootstrap_owner("local@localhost.invalid", "local", "Local user", "local")
            with self.transaction() as db:
                db.execute("UPDATE user_emails SET verified=? WHERE email=?", (time.time(), "local@localhost.invalid"))
            return self.local_auth_user()
        return dict(user)

    def claimable_projects(self, runner):
        with self.transaction() as db:
            identity = db.execute("SELECT projects,organization_id FROM runners WHERE id=? AND revoked=0", (runner,)).fetchone()
            if identity is None or identity["organization_id"] is None:
                return []
            allowed = set(json.loads(identity["projects"]))
            return [row[0] for row in db.execute(
                "SELECT project FROM project_organizations WHERE organization_id=?", (identity["organization_id"],)
            ) if row[0] in allowed]

    def bootstrap_owner(self, email, username, display_name, organization_slug):
        import re

        email = email.strip().lower()
        username = username.strip().lower()
        organization_slug = organization_slug.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise StateConflict("Owner email address is invalid")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", username):
            raise StateConflict("Username must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", organization_slug):
            raise StateConflict("Organization name must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
        now = time.time()
        with self.transaction() as db:
            existing = db.execute("SELECT users.id,users.default_organization_id,user_emails.verified FROM users JOIN user_emails ON user_emails.user_id=users.id WHERE user_emails.email=?", (email,)).fetchone()
            if existing:
                user_id = existing["id"]
                organization_id = existing["default_organization_id"]
                organization = db.execute("SELECT slug FROM organizations WHERE id=?", (organization_id,)).fetchone()
                if organization is None or organization["slug"].lower() != organization_slug:
                    raise StateConflict("The registered user's default organization does not match this slug")
                db.execute("INSERT INTO organization_memberships(organization_id,user_id,role,created) VALUES(?,?,?,?) ON CONFLICT(organization_id,user_id) DO UPDATE SET role='owner'", (organization_id, user_id, "owner", now))
            else:
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    raise StateConflict("Email does not match an existing user. Choose the initial owner before enabling registrations")
                user_id, organization_id = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute("INSERT INTO users(id,username,display_name,default_organization_id,created) VALUES(?,?,?,?,?)", (user_id, username, display_name.strip() or username, organization_id, now))
                db.execute("INSERT INTO user_emails(id,user_id,email,verified,is_primary) VALUES(?,?,?,?,1)", (str(uuid.uuid4()), user_id, email, None))
                db.execute("INSERT INTO organizations(id,slug,display_name,created_by,created) VALUES(?,?,?,?,?)", (organization_id, organization_slug, organization_slug, user_id, now))
                db.execute("INSERT INTO organization_memberships VALUES(?,?,?,?)", (organization_id, user_id, "owner", now))
            slugs = {row[0] for row in db.execute("SELECT slug FROM project_organizations WHERE organization_id=?", (organization_id,))}
            db.execute("INSERT OR IGNORE INTO projects(name,source_type,source,status) SELECT DISTINCT workflows.project,'snapshot','Persisted workflow snapshots','unconfigured' FROM workflows")
            for project in db.execute("SELECT projects.name FROM projects LEFT JOIN project_organizations ON project_organizations.project=projects.name WHERE project_organizations.project IS NULL ORDER BY projects.name").fetchall():
                base_slug = re.sub(r"[^a-z0-9-]+", "-", project["name"].lower()).strip("-")[:50] or "project"
                slug = base_slug
                if slug in slugs:
                    slug = f"{base_slug}-{hashlib.sha256(project['name'].encode()).hexdigest()[:8]}"
                slugs.add(slug)
                db.execute("INSERT INTO project_organizations(project,organization_id,slug) VALUES(?,?,?)", (project["name"], organization_id, slug))
            db.execute("UPDATE runners SET organization_id=? WHERE organization_id IS NULL", (organization_id,))
            db.execute("INSERT INTO audit_events(actor_id,organization_id,action,target_id,created) VALUES(?,?,?,?,?)", (user_id, organization_id, "owner.bootstrapped", user_id, now))
        return {"user_id": user_id, "organization_id": organization_id, "slug": organization_slug}

    def create_login_token(self, email, token_hash, expires, requester_ip=None):
        email = email.strip().lower()
        with self.transaction() as db:
            db.execute("DELETE FROM login_tokens WHERE expires<=? OR (used IS NOT NULL AND created<?)", (time.time(), time.time() - 604800))
            recent = db.execute(
                "SELECT count(*) FROM login_tokens WHERE email=? AND created>?",
                (email, time.time() - 60),
            ).fetchone()[0]
            if recent:
                raise StateConflict("Please wait before requesting another sign-in link")
            if requester_ip:
                requests = db.execute(
                    "SELECT count(*) FROM login_tokens WHERE requester_ip=? AND created>?",
                    (requester_ip, time.time() - 3600),
                ).fetchone()[0]
                if requests >= 20:
                    raise StateConflict("Sign-in email request limit reached")
            db.execute(
                "INSERT INTO login_tokens(id,token_hash,email,expires,created,requester_ip) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), token_hash, email, expires, time.time(), requester_ip),
            )

    def discard_login_token(self, token_hash):
        with self.transaction() as db:
            db.execute("DELETE FROM login_tokens WHERE token_hash=? AND used IS NULL", (token_hash,))

    def consume_login_token(self, token_hash, username, display_name, organization_slug, session_hash, session_expires, registration_enabled=True):
        import re

        now = time.time()
        with self.transaction() as db:
            token = db.execute(
                "SELECT * FROM login_tokens WHERE token_hash=? AND used IS NULL AND expires>?",
                (token_hash, now),
            ).fetchone()
            if token is None:
                raise StateConflict("Login link is invalid or expired")
            email = token["email"].lower()
            user_email = db.execute(
                "SELECT user_id,verified FROM user_emails WHERE email=?",
                (email,),
            ).fetchone()
            if user_email:
                user_id = user_email["user_id"]
                if user_email["verified"] is None:
                    db.execute("UPDATE user_emails SET verified=? WHERE email=?", (now, email))
                if not db.execute("SELECT 1 FROM auth_identities WHERE user_id=? AND provider='email' AND subject=?", (user_id, email)).fetchone():
                    db.execute("INSERT INTO auth_identities(id,user_id,provider,subject,created) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), user_id, "email", email, now))
            else:
                if not registration_enabled:
                    raise StateConflict("Registration is disabled")
                username = username.strip().lower()
                organization_slug = organization_slug.strip().lower()
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", username):
                    raise StateConflict("Username must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", organization_slug):
                    raise StateConflict("Organization name must contain 1 to 63 lowercase letters, numbers, or internal hyphens")
                if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    raise StateConflict("Username is already taken")
                if db.execute("SELECT 1 FROM organizations WHERE slug=?", (organization_slug,)).fetchone():
                    raise StateConflict("Organization name is already taken")
                user_id, organization_id = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute(
                    "INSERT INTO users(id,username,display_name,default_organization_id,created) VALUES(?,?,?,?,?)",
                    (user_id, username, display_name.strip() or username, organization_id, now),
                )
                db.execute(
                    "INSERT INTO user_emails(id,user_id,email,verified,is_primary) VALUES(?,?,?,?,1)",
                    (str(uuid.uuid4()), user_id, email, now),
                )
                db.execute(
                    "INSERT INTO organizations(id,slug,display_name,created_by,created) VALUES(?,?,?,?,?)",
                    (organization_id, organization_slug, display_name.strip() or organization_slug, user_id, now),
                )
                db.execute(
                    "INSERT INTO organization_memberships VALUES(?,?,?,?)",
                    (organization_id, user_id, "owner", now),
                )
                db.execute(
                    "INSERT INTO auth_identities(id,user_id,provider,subject,created) VALUES(?,?,?,?,?)",
                    (str(uuid.uuid4()), user_id, "email", email, now),
                )
            db.execute("UPDATE login_tokens SET used=? WHERE id=? AND used IS NULL", (now, token["id"]))
            db.execute(
                "INSERT INTO browser_sessions(id,token_hash,user_id,expires,created) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), session_hash, user_id, session_expires, now),
            )
            db.execute(
                "INSERT INTO audit_events(actor_id,action,created) VALUES(?,?,?)",
                (user_id, "auth.email_login", now),
            )
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            organizations = db.execute(
                "SELECT o.id,o.slug,o.display_name,m.role FROM organizations o JOIN organization_memberships m ON m.organization_id=o.id WHERE m.user_id=? ORDER BY o.slug",
                (user_id,),
            ).fetchall()
            return dict(user), [dict(row) for row in organizations]

    def authenticate_browser_session(self, token_hash):
        with self.transaction() as db:
            row = db.execute(
                "SELECT users.*,browser_sessions.id AS session_id FROM browser_sessions JOIN users ON users.id=browser_sessions.user_id WHERE token_hash=? AND browser_sessions.revoked IS NULL AND browser_sessions.expires>? AND users.disabled=0",
                (token_hash, time.time()),
            ).fetchone()
            if row is None:
                raise StateConflict("Browser session expired")
            return dict(row)

    def set_project(self, name, source_type, source, ref=None, path=None, commit_sha=None, status="ready", error=None, refreshed=None):
        with self.transaction() as db:
            db.execute(
                "INSERT INTO projects(name,source_type,source,ref,path,commit_sha,status,error,refreshed) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET source_type=excluded.source_type,source=excluded.source,ref=excluded.ref,path=excluded.path,commit_sha=excluded.commit_sha,status=excluded.status,error=excluded.error,refreshed=excluded.refreshed",
                (name, source_type, source, ref, path, commit_sha, status, error, refreshed),
            )

    def project_status(self, name, status, error=None, commit_sha=None, refreshed=None):
        with self.transaction() as db:
            db.execute(
                "UPDATE projects SET status=?,error=?,commit_sha=COALESCE(?,commit_sha),refreshed=COALESCE(?,refreshed) WHERE name=?",
                (status, error, commit_sha, refreshed, name),
            )

    def due_projects(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT project FROM workflows WHERE next_due<=?", (now,)
            )]

    def defer_project(self, project, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            rows = db.execute(
                "SELECT revision,name FROM workflows WHERE project=? AND next_due<=?", (project, now)
            ).fetchall()
            for row in rows:
                _, workflow = self._workflow(db, row["revision"], row["name"])
                next_due = workflow.next_periodic_due(now)
                db.execute(
                    "UPDATE workflows SET next_due=? WHERE project=? AND name=?",
                    (next_due, project, row["name"]),
                )

    def _event(self, db, run, kind, payload=None):
        payload = payload or {}
        db.execute(
            "INSERT INTO events(run,kind,payload,created) VALUES(?,?,?,?)",
            (run, kind, json.dumps(payload), time.time()),
        )
        event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if kind in {
            "run.succeeded", "run.failed", "run.cancelled", "run.needs_input",
            "run.needs_attention", "run.reconciling", "approval.requested",
        }:
            row = db.execute(
                "SELECT runs.project,runs.workflow,project_organizations.organization_id "
                "FROM runs LEFT JOIN project_organizations ON project_organizations.project=runs.project "
                "WHERE runs.id=?",
                (run,),
            ).fetchone()
            preference = db.execute(
                "SELECT enabled FROM notification_event_preferences WHERE organization_id=? AND event=?",
                (row["organization_id"], kind),
            ).fetchone() if row and row["organization_id"] else None
            if row and row["organization_id"] and (preference is None or preference["enabled"]):
                notice = {"run_id": run, "project": row["project"], "workflow": row["workflow"], "event": kind}
                if row["organization_id"]:
                    now = time.time()
                    db.execute(
                        "INSERT OR IGNORE INTO notification_outbox(id,run,event_id,event,payload,next_due,created,organization_id) VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), run, event_id, kind, json.dumps(notice), now, now, row["organization_id"]),
                    )

    def pending_notifications(self, limit=20, now=None):
        now = time.time() if now is None else now
        with self.read_transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT notification_outbox.* FROM notification_outbox WHERE delivered IS NULL AND next_due<=? "
                "AND EXISTS(SELECT 1 FROM notification_targets WHERE notification_targets.organization_id=notification_outbox.organization_id) "
                "AND NOT EXISTS(SELECT 1 FROM notification_event_preferences WHERE notification_event_preferences.organization_id=notification_outbox.organization_id AND notification_event_preferences.event=notification_outbox.event AND notification_event_preferences.enabled=0) "
                "ORDER BY created LIMIT ?",
                (now, limit),
            )]

    def set_notification_target(self, organization_id, kind, url):
        if kind not in {"webhook", "slack"}:
            raise ValueError("Notification channel must be webhook or slack")
        if self.secrets_key is None:
            raise ValueError("Notification encryption is not configured")
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048:
            raise ValueError("Notification URL must be an HTTPS URL under 2048 characters")
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(self.secrets_key).encrypt(
            nonce, url.encode(), f"notification:{organization_id}:{kind}".encode()
        )
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM organizations WHERE id=?", (organization_id,)).fetchone():
                raise StateConflict("Unknown organization")
            db.execute(
                "INSERT INTO notification_targets VALUES(?,?,?,?) ON CONFLICT(organization_id,kind) DO UPDATE SET url_encrypted=excluded.url_encrypted,updated=excluded.updated",
                (organization_id, kind, encrypted.hex(), time.time()),
            )

    def delete_notification_target(self, organization_id, kind):
        if kind not in {"webhook", "slack"}:
            raise ValueError("Notification channel must be webhook or slack")
        with self.transaction() as db:
            return db.execute(
                "DELETE FROM notification_targets WHERE organization_id=? AND kind=?",
                (organization_id, kind),
            ).rowcount > 0

    def notification_targets_for(self, organization_id):
        if self.secrets_key is None:
            return []
        with self.read_transaction() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT kind,url_encrypted FROM notification_targets WHERE organization_id=?",
                (organization_id,),
            )]
        targets = []
        for row in rows:
            raw = bytes.fromhex(row["url_encrypted"])
            value = AESGCM(self.secrets_key).decrypt(
                raw[:12], raw[12:], f"notification:{organization_id}:{row['kind']}".encode()
            ).decode()
            targets.append((row["kind"], value))
        return targets

    def notification_target_status(self, organization_id):
        with self.read_transaction() as db:
            configured = {row[0] for row in db.execute(
                "SELECT kind FROM notification_targets WHERE organization_id=?",
                (organization_id,),
            )}
        return {kind: kind in configured for kind in ("webhook", "slack")}

    def notification_event_preferences(self, organization_id):
        with self.read_transaction() as db:
            values = {row["event"]: bool(row["enabled"]) for row in db.execute(
                "SELECT event,enabled FROM notification_event_preferences WHERE organization_id=?",
                (organization_id,),
            )}
        return {event: values.get(event, True) for event in NOTIFICATION_EVENTS}

    def set_notification_event_preferences(self, organization_id, events):
        if not isinstance(events, list) or any(event not in NOTIFICATION_EVENTS for event in events):
            raise ValueError("Notification events contain an unsupported value")
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM organizations WHERE id=?", (organization_id,)).fetchone():
                raise StateConflict("Unknown organization")
            db.executemany(
                "INSERT INTO notification_event_preferences VALUES(?,?,?) ON CONFLICT(organization_id,event) DO UPDATE SET enabled=excluded.enabled",
                [(organization_id, event, int(event in events)) for event in NOTIFICATION_EVENTS],
            )

    def notification_status(self, limit=100):
        with self.read_transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,run,event,attempts,delivered,error,created FROM notification_outbox ORDER BY created DESC LIMIT ?",
                (limit,),
            )]

    def notification_result(self, notification_id, error=None, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            row = db.execute("SELECT attempts FROM notification_outbox WHERE id=? AND delivered IS NULL", (notification_id,)).fetchone()
            if row is None:
                return
            attempts = row["attempts"] + 1
            if error is None:
                db.execute("UPDATE notification_outbox SET attempts=?,delivered=?,error=NULL WHERE id=?", (attempts, now, notification_id))
            elif attempts >= 8:
                db.execute("UPDATE notification_outbox SET attempts=?,delivered=?,error=? WHERE id=?", (attempts, now, str(error)[:500], notification_id))
            else:
                delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
                db.execute("UPDATE notification_outbox SET attempts=?,next_due=?,error=? WHERE id=?", (attempts, now + delay, str(error)[:500], notification_id))

    def prune_webhook_deliveries(self, now=None, retention_seconds=30 * 86400):
        now = time.time() if now is None else now
        with self.transaction() as db:
            return db.execute(
                "DELETE FROM webhook_deliveries WHERE created<?", (now - retention_seconds,)
            ).rowcount

    def prune_notifications(self, now=None, retention_seconds=30 * 86400):
        now = time.time() if now is None else now
        with self.transaction() as db:
            return db.execute(
                "DELETE FROM notification_outbox WHERE created<? AND (delivered IS NOT NULL OR attempts>=8 OR NOT EXISTS(SELECT 1 FROM notification_targets WHERE notification_targets.organization_id=notification_outbox.organization_id))",
                (now - retention_seconds,),
            ).rowcount

    def register(self, project: str, package: dict, now: float | None = None):
        """Register a valid revision; unchanged registrations preserve schedules."""
        config = ProjectConfig.model_validate(package["configuration"])
        now = time.time() if now is None else now
        revision = f"{project}:{package['sha256']}"
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO revisions VALUES(?,?,?)",
                (revision, project, json.dumps(package)),
            )
            names = list(config.workflows)
            for name, workflow in config.workflows.items():
                previous = db.execute(
                    "SELECT * FROM workflows WHERE project=? AND name=?",
                    (project, name),
                ).fetchone()
                next_due = workflow.next_periodic_due(now)
                due = (
                    previous["next_due"]
                    if previous and previous["revision"] == revision and next_due is not None
                    else None
                )
                if next_due is not None and due is None:
                    due = next_due
                db.execute(
                    "INSERT INTO workflows(project,name,revision,next_due) VALUES(?,?,?,?) ON CONFLICT(project,name) DO UPDATE SET revision=excluded.revision,next_due=excluded.next_due",
                    (project, name, revision, due),
                )
                db.execute(
                    "INSERT OR IGNORE INTO workflow_retention(project,workflow) VALUES(?,?)",
                    (project, name),
                )
            for row in db.execute(
                "SELECT name FROM workflows WHERE project=?", (project,)
            ).fetchall():
                if row["name"] not in names:
                    db.execute(
                        "DELETE FROM workflows WHERE project=? AND name=?",
                        (project, row["name"]),
                    )
        return revision

    def _workflow(self, db, revision, name):
        package = json.loads(
            db.execute(
                "SELECT snapshot FROM revisions WHERE id=?", (revision,)
            ).fetchone()[0]
        )
        return package, ProjectConfig.model_validate(
            package["configuration"]
        ).workflows[name]

    def _enqueue(self, db, row, inputs, occurrence=None, item=None, item_snapshot=None):
        run = str(uuid.uuid4())
        db.execute(
            "INSERT INTO runs(id,project,workflow,revision,occurrence,item,item_snapshot,inputs,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run,
                row["project"],
                row["name"],
                row["revision"],
                occurrence,
                item,
                json.dumps(item_snapshot) if item_snapshot is not None else None,
                json.dumps(inputs),
                "queued",
                time.time(),
            ),
        )
        _, workflow = self._workflow(db, row["revision"], row["name"])
        for name in workflow.steps:
            db.execute(
                "INSERT INTO steps(run,name,status) VALUES(?,?,?)",
                (run, name, "pending"),
            )
        for name in workflow.on_failure:
            db.execute(
                "INSERT INTO steps(run,name,status) VALUES(?,?,?)",
                (run, f"failure__{name}", "skipped"),
            )
        self._event(db, run, "run.queued")
        db.execute("INSERT INTO run_status_history(run,status,created) VALUES(?,?,?)", (run, "queued", time.time()))
        return run

    def enqueue(self, project: str, workflow: str, inputs: dict | None = None):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE project=? AND name=?",
                (project, workflow),
            ).fetchone()
            if row is None:
                raise StateConflict("Unknown workflow")
            _, definition = self._workflow(db, row["revision"], workflow)
            try:
                validate(inputs or {}, definition.input_schema)
            except Exception as error:
                raise ValueError("Workflow inputs do not match input_schema") from error
            occurrence = str(uuid.uuid4())
            db.execute(
                "INSERT INTO occurrences VALUES(?,?,?,?,?)",
                (occurrence, project, workflow, "manual", time.time()),
            )
            return self._enqueue(db, row, inputs or {}, occurrence)

    def enqueue_webhook(self, provider, delivery, body_hash, project, workflow, inputs):
        with self.transaction() as db:
            previous = db.execute(
                "SELECT body_hash,run FROM webhook_deliveries WHERE provider=? AND delivery=?",
                (provider, delivery),
            ).fetchone()
            if previous:
                if previous["body_hash"] != body_hash:
                    raise StateConflict("Webhook delivery identifier was reused with different content")
                return previous["run"], False
            row = db.execute(
                "SELECT * FROM workflows WHERE project=? AND name=?", (project, workflow)
            ).fetchone()
            if row is None:
                raise StateConflict("Webhook target workflow is unavailable")
            _, definition = self._workflow(db, row["revision"], workflow)
            try:
                validate(inputs, definition.input_schema)
            except Exception as error:
                raise ValueError("Webhook inputs do not match the target workflow input_schema") from error
            occurrence = str(uuid.uuid4())
            db.execute(
                "INSERT INTO occurrences VALUES(?,?,?,?,?)",
                (occurrence, project, workflow, f"webhook:{provider}", time.time()),
            )
            run = self._enqueue(db, row, inputs, occurrence)
            db.execute(
                "INSERT INTO webhook_deliveries VALUES(?,?,?,?,?)",
                (provider, delivery, body_hash, run, time.time()),
            )
            return run, True

    def answer_needs_input(self, run_id, answers):
        if not isinstance(answers, dict) or not answers:
            raise ValueError("Answers must be a nonempty object")
        answer_hash = hashlib.sha256(
            json.dumps(answers, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.transaction() as db:
            original = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if original is None:
                raise StateConflict("Unknown run")
            previous = db.execute(
                "SELECT answer_hash,new_run FROM run_answers WHERE source_run=?", (run_id,)
            ).fetchone()
            if previous:
                if previous["answer_hash"] != answer_hash:
                    raise StateConflict("This run already has a different submitted answer")
                return previous["new_run"]
            if original["status"] != "needs_input":
                raise StateConflict("This run is not waiting for input")
            ambiguities = []
            for row in db.execute("SELECT result FROM steps WHERE run=?", (run_id,)).fetchall():
                if not row["result"]:
                    continue
                result = json.loads(row["result"])
                if result.get("status") == "needs_input":
                    ambiguities.extend(result.get("outputs", {}).get("ambiguities", []))
            questions = {
                item.get("id"): item
                for item in ambiguities
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("question"), str)
            }
            if not questions or set(answers) != set(questions):
                raise ValueError("Answers must match every outstanding question")
            if any(not isinstance(value, str) or not value.strip() or len(value) > 10000 for value in answers.values()):
                raise ValueError("Each answer must contain 1 to 10000 characters")
            workflow_row = db.execute(
                "SELECT * FROM workflows WHERE project=? AND name=?",
                (original["project"], original["workflow"]),
            ).fetchone()
            if workflow_row is None:
                raise StateConflict("Workflow is no longer registered")
            active = db.execute(
                "SELECT 1 FROM runs WHERE item=? AND status IN ('queued','running','reconciling','waiting_approval')",
                (original["item"],),
            ).fetchone() if original["item"] else None
            if active:
                raise StateConflict("Another run for this work item is already active")
            occurrence = str(uuid.uuid4())
            db.execute(
                "INSERT INTO occurrences VALUES(?,?,?,?,?)",
                (occurrence, original["project"], original["workflow"], "answer", time.time()),
            )
            inputs = json.loads(original["inputs"])
            inputs["answers"] = answers
            item_snapshot = json.loads(original["item_snapshot"]) if original["item_snapshot"] else None
            resumed = self._enqueue(
                db, workflow_row, inputs, occurrence, original["item"], item_snapshot
            )
            db.execute(
                "INSERT INTO run_answers VALUES(?,?,?,?)",
                (run_id, answer_hash, resumed, time.time()),
            )
            self._event(db, resumed, "run.resumed", {"parent_run": run_id})
            return resumed

    def tick(self, now: float | None = None):
        """Coalesce missed periods and suppress overlapping periodic runs."""
        now = time.time() if now is None else now
        runs = []
        with self.transaction() as db:
            for row in db.execute(
                "SELECT * FROM workflows WHERE next_due<=?", (now,)
            ).fetchall():
                _, workflow = self._workflow(db, row["revision"], row["name"])
                active = db.execute(
                    "SELECT 1 FROM runs WHERE project=? AND workflow=? AND status IN ('queued','running','reconciling','waiting_approval')",
                    (row["project"], row["name"]),
                ).fetchone()
                if not active:
                    occurrence = str(uuid.uuid4())
                    db.execute(
                        "INSERT INTO occurrences VALUES(?,?,?,?,?)",
                        (occurrence, row["project"], row["name"], "periodic", now),
                    )
                    runs.append(self._enqueue(db, row, {}, occurrence))
                next_due = workflow.next_periodic_due(now)
                db.execute(
                    "UPDATE workflows SET next_due=? WHERE project=? AND name=?",
                    (next_due, row["project"], row["name"]),
                )
        return runs

    def _advance(self, db, run):
        _, workflow = self._workflow(db, run["revision"], run["workflow"])
        steps = {
            row["name"]: dict(row)
            for row in db.execute("SELECT * FROM steps WHERE run=?", (run["id"],))
        }
        context = {
            "inputs": json.loads(run["inputs"]),
            "steps": {
                name: json.loads(step["result"])
                for name, step in steps.items()
                if step["result"]
            },
        }
        failures = [
            (name, steps[name])
            for name in workflow.steps
            if steps[name]["status"] in ("failed", "needs_attention")
        ]
        if failures and workflow.on_failure:
            failed_name = failures[0][0]
            context["failure"] = self._failure_context(db, run, workflow)
            for normal_name in workflow.steps:
                if steps[normal_name]["status"] == "pending":
                    db.execute(
                        "UPDATE steps SET status='skipped' WHERE run=? AND name=?",
                        (run["id"], normal_name),
                    )
                    steps[normal_name]["status"] = "skipped"
            for handler_name, definition in workflow.on_failure.items():
                key = f"failure__{handler_name}"
                handler = steps[key]
                if handler["status"] == "skipped":
                    db.execute(
                        "UPDATE steps SET status='pending' WHERE run=? AND name=?",
                        (run["id"], key),
                    )
                    handler["status"] = "pending"
                if handler["status"] not in ("pending", "ready"):
                    continue
                dependencies = [
                    steps[f"failure__{dependency}"]["status"]
                    for dependency in definition.needs
                ]
                if any(status not in ("succeeded", "skipped") for status in dependencies):
                    if any(status in ("failed", "needs_attention", "cancelled") for status in dependencies):
                        db.execute(
                            "UPDATE steps SET status='skipped' WHERE run=? AND name=?",
                            (run["id"], key),
                        )
                        handler["status"] = "skipped"
                    continue
                db.execute(
                    "UPDATE steps SET status='ready' WHERE run=? AND name=?",
                    (run["id"], key),
                )
                handler["status"] = "ready"
            handler_statuses = [
                steps[f"failure__{name}"]["status"] for name in workflow.on_failure
            ]
            if all(
                status in ("succeeded", "failed", "needs_attention", "cancelled", "skipped")
                for status in handler_statuses
            ):
                db.execute("UPDATE runs SET status='needs_attention' WHERE id=?", (run["id"],))
                db.execute("DELETE FROM resource_locks WHERE run=?", (run["id"],))
                self._event(db, run["id"], "run.needs_attention", {"failed_step": failed_name})
            return
        for name, definition in workflow.steps.items():
            step = steps[name]
            if step["status"] != "pending":
                continue
            dependencies = [
                steps[dependency]["status"] for dependency in definition.needs
            ]
            if any(status not in ("succeeded", "skipped") for status in dependencies):
                continue
            try:
                enabled = (
                    (not any(status == "skipped" for status in dependencies))
                    if not definition.when
                    else evaluate_condition(definition.when, context)
                )
            except (KeyError, TypeError, ValueError) as error:
                db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
                self._event(
                    db,
                    run["id"],
                    "condition.failed",
                    {"step": name, "error": str(error)},
                )
                return
            status = "ready" if enabled else "skipped"
            if enabled and definition.type == "approval":
                try:
                    summary = self._resolve_reference(context, definition.summary_ref)
                    fingerprint = self._resolve_reference(context, definition.content_ref)
                    if not isinstance(summary, str) or not summary.strip():
                        raise ValueError("Approval summary must be a nonempty string")
                    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(
                        char not in "0123456789abcdef" for char in fingerprint
                    ):
                        raise ValueError("Approval content reference must be a SHA-256 digest")
                except (KeyError, TypeError, ValueError) as error:
                    db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
                    self._event(db, run["id"], "approval.invalid", {"step": name, "error": str(error)})
                    return
                result = {
                    "status": "waiting_approval",
                    "summary": summary,
                    "outputs": {"content_fingerprint": fingerprint},
                    "approvers": definition.approvers,
                    "requested_at": time.time(),
                    "timeout_seconds": definition.approval_timeout_seconds,
                }
                db.execute(
                    "UPDATE steps SET status='waiting_approval',result=? WHERE run=? AND name=?",
                    (json.dumps(result, sort_keys=True), run["id"], name),
                )
                steps[name]["status"] = "waiting_approval"
                steps[name]["result"] = json.dumps(result)
                self._event(db, run["id"], "approval.requested", {"step": name, "content_fingerprint": fingerprint})
                continue
            db.execute(
                "UPDATE steps SET status=? WHERE run=? AND name=?",
                (status, run["id"], name),
            )
            steps[name]["status"] = status
            if not enabled:
                self._event(db, run["id"], "step.skipped", {"step": name})
        if all(step["status"] in ("succeeded", "skipped") for step in steps.values()):
            db.execute("UPDATE runs SET status='succeeded' WHERE id=?", (run["id"],))
            self._event(db, run["id"], "run.succeeded")
            db.execute("DELETE FROM resource_locks WHERE run=?", (run["id"],))
            if workflow.discovery:
                discovered = []
                for step in steps.values():
                    if step["result"]:
                        outputs = json.loads(step["result"])["outputs"]
                        batch = outputs.get("work_items", [])
                        if not isinstance(batch, list):
                            raise StateConflict("Discovery work_items must be an array")
                        discovered.extend(batch)
                self._ingest(
                    db,
                    run["project"],
                    workflow.discovery.target_workflow,
                    workflow.discovery.source,
                    discovered,
                    run["occurrence"],
                )
        elif any(step["status"] == "waiting_approval" for step in steps.values()) and not any(
            step["status"] in ("ready", "running") for step in steps.values()
        ):
            db.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (run["id"],))
            db.execute("DELETE FROM resource_locks WHERE run=?", (run["id"],))

    @staticmethod
    def _failure_context(db, run, workflow):
        failures = []
        for name in workflow.steps:
            step = db.execute(
                "SELECT status,result FROM steps WHERE run=? AND name=?",
                (run["id"], name),
            ).fetchone()
            if step is None or step["status"] not in ("failed", "needs_attention"):
                continue
            result = json.loads(step["result"] or "{}")
            attempt = db.execute(
                "SELECT id FROM attempts WHERE run=? AND step=? ORDER BY generation DESC LIMIT 1",
                (run["id"], name),
            ).fetchone()
            failures.append({
                "step": name,
                "status": step["status"],
                "summary": result.get("summary", "Workflow step failed"),
                "error": result.get("error") or "UnknownError",
                "attempt_id": attempt["id"] if attempt else "unknown",
                "diagnostics": result.get("outputs", {}),
                "artifacts": result.get("artifacts", []),
            })
        if failures:
            return {**failures[0], "run_id": run["id"], "failures": failures}
        return {
            "step": "unknown",
            "status": "failed",
            "summary": "Workflow step failed",
            "error": "UnknownError",
            "attempt_id": "unknown",
            "run_id": run["id"],
            "diagnostics": {},
            "artifacts": [],
            "failures": [],
        }

    @staticmethod
    def _resolve_reference(context, reference):
        parts = reference.split(".")
        if len(parts) < 2 or parts[0] not in {"steps", "inputs", "failure"}:
            raise ValueError("Reference must use a dotted steps, inputs or failure path")
        value = context[parts[0]]
        for part in parts[1:]:
            if part.startswith("_"):
                raise ValueError("Private references are forbidden")
            value = value[part] if isinstance(value, dict) else getattr(value, part)
        return value

    def expire_approvals(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            rows = db.execute(
                "SELECT runs.id,steps.name,steps.result FROM runs JOIN steps ON steps.run=runs.id WHERE runs.status='waiting_approval' AND steps.status='waiting_approval'"
            ).fetchall()
            expired = []
            for row in rows:
                pending = json.loads(row["result"])
                if pending.get("requested_at", now) + pending.get("timeout_seconds", 86400) > now:
                    continue
                result = {
                    "status": "needs_attention",
                    "summary": "Approval timed out; publication was not authorized",
                    "outputs": {"decision": "expired"},
                }
                db.execute(
                    "UPDATE steps SET status='needs_attention',result=? WHERE run=? AND name=?",
                    (json.dumps(result, sort_keys=True), row["id"], row["name"]),
                )
                db.execute("UPDATE runs SET status='needs_attention' WHERE id=?", (row["id"],))
                db.execute("DELETE FROM resource_locks WHERE run=?", (row["id"],))
                self._event(db, row["id"], "approval.expired", {"step": row["name"]})
                expired.append(row["id"])
            return expired

    def decide_approval(self, run_id, step_name, user_id, role, decision, note=""):
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("Approval note must be at most 4000 characters")
        with self.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise StateConflict("Unknown run")
            _package, workflow = self._workflow(db, run["revision"], run["workflow"])
            definition = workflow.steps.get(step_name)
            if definition is None or definition.type != "approval":
                raise StateConflict("Unknown approval step")
            step = db.execute(
                "SELECT * FROM steps WHERE run=? AND name=?", (run_id, step_name)
            ).fetchone()
            if run["status"] != "waiting_approval" or step["status"] != "waiting_approval":
                raise StateConflict("Approval is no longer pending")
            if role not in definition.approvers:
                raise StateConflict("Your role cannot approve this step")
            pending = json.loads(step["result"])
            result = {
                "status": "succeeded" if decision == "approved" else "needs_attention",
                "summary": note or f"Approval {decision}",
                "outputs": {
                    "decision": decision,
                    "content_fingerprint": pending["outputs"]["content_fingerprint"],
                },
            }
            step_status = "succeeded" if decision == "approved" else "needs_attention"
            db.execute(
                "UPDATE steps SET status=?,result=? WHERE run=? AND name=?",
                (step_status, json.dumps(result, sort_keys=True), run_id, step_name),
            )
            self._event(db, run_id, f"approval.{decision}", {
                "step": step_name,
                "user_id": user_id,
                "role": role,
                "note": note,
                "content_fingerprint": pending["outputs"]["content_fingerprint"],
            })
            if decision == "rejected":
                db.execute("UPDATE runs SET status='needs_attention' WHERE id=?", (run_id,))
                db.execute("DELETE FROM resource_locks WHERE run=?", (run_id,))
                return result
            db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            self._advance(db, run)
            return result

    def claim(
        self,
        runner: str,
        allowed_projects: list[str],
        capacity: int = 1,
        lease_seconds: float = 60,
        now: float | None = None,
        adapters: list[str] | None = None,
        resources: list[str] | None = None,
        action_contracts: dict[str, int] | None = None,
        protocol_version: str | None = None,
    ):
        """Atomically claim one step under engine-provided permissions and capacity."""
        if capacity < 1 or lease_seconds <= 0:
            raise ValueError("Capacity and lease duration must be positive")
        now = time.time() if now is None else now
        with self.transaction() as db:
            self._expire(db, now)
            runner_row = db.execute("SELECT projects,organization_id,revoked FROM runners WHERE id=?", (runner,)).fetchone()
            if runner_row is None or runner_row["revoked"] or runner_row["organization_id"] is None:
                return None
            requested = set(allowed_projects) & set(json.loads(runner_row["projects"]))
            allowed_projects = {
                row[0]
                for row in db.execute(
                    "SELECT project FROM project_organizations WHERE organization_id=?",
                    (runner_row["organization_id"],),
                )
                if row[0] in requested
            }
            busy = db.execute(
                "SELECT count(*) FROM attempts WHERE runner=? AND status IN ('running','reconciling')",
                (runner,),
            ).fetchone()[0]
            if busy >= capacity:
                return None
            for run in db.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY created,id"
            ).fetchall():
                if run["project"] not in allowed_projects:
                    continue
                affinity = db.execute(
                    "SELECT runner FROM affinity WHERE run=?", (run["id"],)
                ).fetchone()
                if not affinity and run["item"]:
                    affinity = db.execute(
                        "SELECT affinity.runner FROM affinity JOIN runs ON affinity.run=runs.id WHERE runs.item=? ORDER BY runs.created LIMIT 1",
                        (run["item"],),
                    ).fetchone()
                if affinity and affinity[0] != runner:
                    continue
                package, workflow = self._workflow(db, run["revision"], run["workflow"])
                all_steps = workflow.all_steps()
                if protocol_version != PROTOCOL_VERSION and any(
                    step.type == "agent" and step.prompt_text is not None
                    for step in all_steps.values()
                ):
                    continue
                required_contracts = package.get("action_contracts", {}).get(
                    run["workflow"], {}
                )
                if action_contracts is not None and any(
                    action_contracts.get(name, 0) < version
                    for name, version in required_contracts.items()
                ):
                    continue
                required = {
                    "action"
                    if step.type == "action"
                    else "shell"
                    if step.type == "shell"
                    else package["configuration"]["profiles"][step.profile]["adapter"]
                    for step in all_steps.values()
                    if step.type != "approval"
                }
                if adapters is not None and not required.issubset(adapters):
                    continue
                required_resources = {
                    resource
                    for definition in all_steps.values()
                    for resource in definition.resources
                }
                if resources is not None and not required_resources.issubset(resources):
                    continue
                if any(
                    db.execute(
                        "SELECT 1 FROM resource_locks WHERE project=? AND resource=? AND run<>?",
                        (runner_row["organization_id"], resource, run["id"]),
                    ).fetchone()
                    for resource in required_resources
                ):
                    continue
                if run["status"] == "queued":
                    active = db.execute(
                        "SELECT count(*) FROM runs WHERE project=? AND workflow=? AND status IN ('running','reconciling','waiting_approval')",
                        (run["project"], run["workflow"]),
                    ).fetchone()[0]
                    if active >= workflow.max_active_runs:
                        continue
                    db.execute(
                        "UPDATE runs SET status='running' WHERE id=?", (run["id"],)
                    )
                    self._event(db, run["id"], "run.running")
                self._advance(db, run)
                current = db.execute(
                    "SELECT status FROM runs WHERE id=?", (run["id"],)
                ).fetchone()[0]
                if current != "running":
                    continue
                step = db.execute(
                    "SELECT * FROM steps WHERE run=? AND status='ready' ORDER BY name LIMIT 1",
                    (run["id"],),
                ).fetchone()
                if step is None:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO affinity VALUES(?,?)", (run["id"], runner)
                )
                for resource in required_resources:
                    db.execute(
                        "INSERT OR IGNORE INTO resource_locks VALUES(?,?,?,?)",
                        (runner_row["organization_id"], resource, run["id"], runner),
                    )
                attempt, lease = str(uuid.uuid4()), str(uuid.uuid4())
                generation = step["iteration"] + 1
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        attempt,
                        run["id"],
                        step["name"],
                        runner,
                        lease,
                        generation,
                        now + lease_seconds,
                        "running",
                        None,
                    ),
                )
                if run["keep_forever"]:
                    self._queue_log_policy(db, attempt, runner, retained=True)
                    db.execute("UPDATE runs SET retention_pending=1 WHERE id=?", (run["id"],))
                db.execute(
                    "UPDATE steps SET status='running',iteration=? WHERE run=? AND name=?",
                    (generation, run["id"], step["name"]),
                )
                self._event(
                    db,
                    run["id"],
                    "step.claimed",
                    {"attempt": attempt, "runner": runner},
                )
                project_environment = self.list_project_environment(run["project"], include_values=True)
                return {
                    "attempt_id": attempt,
                    "lease_id": lease,
                    "generation": generation,
                    "expires_at": now + lease_seconds,
                    "run_id": run["id"],
                    "workflow_name": run["workflow"],
                    "workspace_key": run["item"] or run["id"],
                    "step_name": step["name"],
                    "step": all_steps[step["name"]].model_dump(mode="json"),
                    "inputs": json.loads(run["inputs"]),
                    "previous_results": {
                        row["name"]: json.loads(row["result"])
                        for row in db.execute(
                            "SELECT name,result FROM steps WHERE run=? AND result IS NOT NULL",
                            (run["id"],),
                        )
                    },
                    "failure": (
                        self._failure_context(db, run, workflow)
                        if step["name"].startswith("failure__")
                        else None
                    ),
                    "package": package,
                    "project_environment": {
                        item["name"]: item["value"] for item in project_environment
                    },
                    "project_secret_names": [
                        item["name"] for item in project_environment if item["secret"]
                    ],
                }
        return None

    def _expire(self, db, now):
        for attempt in db.execute(
            "SELECT * FROM attempts WHERE status='running' AND expires<=?", (now,)
        ).fetchall():
            db.execute(
                "UPDATE attempts SET status='reconciling' WHERE id=?", (attempt["id"],)
            )
            db.execute(
                "UPDATE runs SET status='reconciling' WHERE id=?", (attempt["run"],)
            )
            self._event(db, attempt["run"], "lease.expired", {"attempt": attempt["id"]})

    def expire(self, now: float | None = None):
        with self.transaction() as db:
            self._expire(db, time.time() if now is None else now)

    def _assignment(self, db, runner, lease, generation):
        attempt = db.execute(
            "SELECT * FROM attempts WHERE lease=? AND runner=? AND generation=?",
            (lease, runner, generation),
        ).fetchone()
        if attempt is None:
            raise StateConflict("Unknown or stale assignment")
        return attempt

    def renew(self, runner, lease, generation, lease_seconds=60, now=None):
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive")
        return self.renew_assignment(runner, lease, generation, lease_seconds, now)

    def complete(self, runner, lease, generation, result: StepResult, now=None):
        encoded = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        with self.transaction() as db:
            self._expire(db, time.time() if now is None else now)
            attempt = self._assignment(db, runner, lease, generation)
            if attempt["result"] is not None:
                if attempt["result"] != encoded:
                    raise StateConflict("Conflicting duplicate result")
                return
            run = db.execute(
                "SELECT * FROM runs WHERE id=?", (attempt["run"],)
            ).fetchone()
            if attempt["status"] != "running" or run["status"] != "running":
                raise StateConflict("Assignment requires reconciliation or is terminal")
            package, workflow = self._workflow(db, run["revision"], run["workflow"])
            definition = workflow.all_steps()[attempt["step"]]
            if definition.action:
                from aorch.extensions.schemas import validate_action_result

                version = package.get("action_contracts", {}).get(
                    run["workflow"], {}
                ).get(definition.action, 1)
                try:
                    validate_action_result(definition.action, result, version)
                except ValueError as error:
                    raise StateConflict(str(error)) from error
            if definition.result_schema:
                validate(result.model_dump(mode="json"), definition.result_schema)
            for effect in result.outputs.get("external_effects", []):
                key = f"{effect['kind']}:{effect['key']}"
                db.execute(
                    "INSERT INTO effects VALUES(?,?,?,?) ON CONFLICT(run,effect_key) DO UPDATE SET status=excluded.status,provider_id=excluded.provider_id",
                    (run["id"], key, "done", effect["provider_id"]),
                )
                if run["item"]:
                    db.execute(
                        "INSERT INTO item_effects VALUES(?,?,?) ON CONFLICT(item,effect_key) DO UPDATE SET provider_id=excluded.provider_id",
                        (run["item"], key, effect["provider_id"]),
                    )
            status = result.status
            if status == "succeeded" and definition.repeat:
                context = {
                    "inputs": json.loads(run["inputs"]),
                    "steps": {
                        row["name"]: json.loads(row["result"])
                        for row in db.execute(
                            "SELECT name,result FROM steps WHERE run=? AND result IS NOT NULL",
                            (run["id"],),
                        )
                    },
                }
                context["steps"][attempt["step"]] = result.model_dump(mode="json")
                try:
                    satisfied = evaluate_condition(definition.repeat.until, context)
                except (KeyError, TypeError, ValueError) as error:
                    raise StateConflict(f"Invalid repeat result: {error}") from error
                if not satisfied:
                    status = (
                        "ready"
                        if generation < definition.repeat.max_iterations
                        else "needs_attention"
                    )
            db.execute(
                "UPDATE attempts SET status=?,result=? WHERE id=?",
                (result.status, encoded, attempt["id"]),
            )
            db.execute(
                "UPDATE steps SET status=?,result=? WHERE run=? AND name=?",
                (status, encoded, run["id"], attempt["step"]),
            )
            self._event(
                db,
                run["id"],
                "step.completed",
                {"step": attempt["step"], "attempt": attempt["id"], "status": status},
            )
            is_failure_handler = attempt["step"].startswith("failure__")
            starts_failure_handling = (
                not is_failure_handler
                and status in ("failed", "needs_attention")
                and bool(workflow.on_failure)
            )
            if starts_failure_handling or (is_failure_handler and status not in ("succeeded", "ready")):
                siblings = db.execute(
                    "SELECT 1 FROM attempts WHERE run=? AND status IN ('running','reconciling')",
                    (run["id"],),
                ).fetchone()
                if siblings:
                    run_status = "reconciling"
                else:
                    run_status = "running"
                db.execute("UPDATE runs SET status=? WHERE id=?", (run_status, run["id"]))
                self._event(
                    db,
                    run["id"],
                    "run.failure_handling.started" if starts_failure_handling else "run.failure_handling.continued",
                    {"step": attempt["step"]},
                )
                if not siblings:
                    self._advance(db, run)
            elif status not in ("succeeded", "ready"):
                siblings = db.execute(
                    "SELECT 1 FROM attempts WHERE run=? AND status IN ('running','reconciling')",
                    (run["id"],),
                ).fetchone()
                uncertain_resource = result.error == "ResourceUncertain"
                run_status = "reconciling" if siblings or uncertain_resource else status
                db.execute(
                    "UPDATE runs SET status=? WHERE id=?", (run_status, run["id"])
                )
                self._event(db, run["id"], f"run.{status}")
                if uncertain_resource:
                    db.execute(
                        "UPDATE attempts SET status='reconciling' WHERE id=?",
                        (attempt["id"],),
                    )
                if not siblings and not uncertain_resource:
                    db.execute("DELETE FROM resource_locks WHERE run=?", (run["id"],))
            else:
                self._advance(db, run)

    def ingest(
        self,
        project: str,
        workflow: str,
        source: str,
        items: list[dict],
        occurrence: str | None = None,
    ):
        """Deduplicate discoveries and enqueue fresh snapshots for inactive items."""
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
            ):
                raise ValueError("Work item requires a nonempty string id")
        with self.transaction() as db:
            return self._ingest(db, project, workflow, source, items, occurrence)

    def _ingest(self, db, project, workflow, source, items, occurrence):
        runs = []
        row = db.execute(
            "SELECT * FROM workflows WHERE project=? AND name=?", (project, workflow)
        ).fetchone()
        if row is None:
            raise StateConflict("Unknown workflow")
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
            ):
                raise StateConflict("Work item requires a nonempty string id")
            previous_snapshot = db.execute(
                "SELECT snapshot FROM items WHERE project=? AND workflow=? AND source=? AND external_id=?",
                (project, workflow, source, item["id"]),
            ).fetchone()
            unchanged_item = bool(
                previous_snapshot
                and json.loads(previous_snapshot["snapshot"]) == item
            )
            db.execute(
                "INSERT INTO items VALUES(?,?,?,?,?,?) ON CONFLICT(project,workflow,source,external_id) DO UPDATE SET snapshot=excluded.snapshot",
                (
                    str(uuid.uuid4()),
                    project,
                    workflow,
                    source,
                    item["id"],
                    json.dumps(item),
                ),
            )
            stored = db.execute(
                "SELECT id FROM items WHERE project=? AND workflow=? AND source=? AND external_id=?",
                (project, workflow, source, item["id"]),
            ).fetchone()[0]
            active = db.execute(
                "SELECT 1 FROM runs WHERE item=? AND status IN ('queued','running','reconciling','waiting_approval')",
                (stored,),
            ).fetchone()
            waiting_for_input = db.execute(
                "SELECT 1 FROM runs WHERE item=? AND status='needs_input'",
                (stored,),
            ).fetchone()
            published = db.execute(
                "SELECT 1 FROM item_effects WHERE item=? AND (effect_key LIKE 'git.pr:%' OR effect_key LIKE '[aorch:pr:%')",
                (stored,),
            ).fetchone()
            if not active and not (waiting_for_input and unchanged_item) and not published:
                history = [
                    json.loads(entry[0])
                    for entry in db.execute(
                        "SELECT result FROM item_history WHERE item=? ORDER BY created,run,step",
                        (stored,),
                    )
                ]
                fresh_item = {**item, "previous_results": history}
                runs.append(
                    self._enqueue(
                        db,
                        row,
                        {"work_item": fresh_item},
                        occurrence,
                        stored,
                        fresh_item,
                    )
                )
        return runs

    def get_run(self, run):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if row is None:
                raise StateConflict("Unknown run")
            return {
                **dict(row),
                "steps": [
                    dict(step)
                    for step in db.execute("SELECT * FROM steps WHERE run=?", (run,))
                ],
            }

    def _queue_log_policy(self, db, attempt, runner, retained=False, deleted=False):
        db.execute(
            "INSERT INTO runner_log_policy(attempt,runner,retained,deleted) VALUES(?,?,?,?) "
            "ON CONFLICT(attempt) DO UPDATE SET runner=excluded.runner,retained=excluded.retained,deleted=excluded.deleted,generation=runner_log_policy.generation+1,acknowledged=0",
            (attempt, runner, int(retained), int(deleted)),
        )

    def set_keep_forever(self, run, keep):
        if not isinstance(keep, bool):
            raise TypeError("keep_forever must be a boolean")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if row is None:
                raise StateConflict("Unknown run")
            db.execute("UPDATE runs SET keep_forever=?,retention_pending=? WHERE id=?", (int(keep), 0, run))
            for attempt in db.execute("SELECT id,runner FROM attempts WHERE run=?", (run,)).fetchall():
                self._queue_log_policy(db, attempt["id"], attempt["runner"], retained=keep)
            pending = db.execute(
                "SELECT count(*) FROM runner_log_policy WHERE attempt IN (SELECT id FROM attempts WHERE run=?) AND acknowledged=0",
                (run,),
            ).fetchone()[0]
            db.execute("UPDATE runs SET retention_pending=? WHERE id=?", (int(bool(keep and pending)), run))
            paths = self._prune_workflow(db, row["project"], row["workflow"])
            result = {"run_id": run, "keep_forever": keep, "retention_pending": bool(keep and pending)}
        self._remove_artifact_files(paths)
        return result

    def set_workflow_retention(self, project, workflow, count):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError("Retention count must be between 1 and 10000")
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM workflows WHERE project=? AND name=?", (project, workflow)
            ).fetchone():
                raise StateConflict("Unknown workflow")
            changed = db.execute(
                "UPDATE workflow_retention SET retention_count=? WHERE project=? AND workflow=?",
                (count, project, workflow),
            ).rowcount
            if not changed:
                raise StateConflict("Unknown workflow")
            paths = self._prune_workflow(db, project, workflow)
        self._remove_artifact_files(paths)
        return {"project": project, "workflow": workflow, "retention_count": count}

    def _remove_artifact_files(self, paths):
        for path in paths:
            try:
                (self.artifact_root / path).unlink(missing_ok=True)
            except OSError:
                pass

    def _delete_run_db(self, db, run):
        row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
        paths = [item[0] for item in db.execute(
            "SELECT path FROM artifacts JOIN attempts ON artifacts.attempt=attempts.id WHERE attempts.run=?", (run,)
        )]
        attempts = db.execute("SELECT id,runner FROM attempts WHERE run=?", (run,)).fetchall()
        if row["item"]:
            db.execute(
                "INSERT OR IGNORE INTO item_history(item,run,step,result,created) "
                "SELECT ?,steps.run,steps.name,steps.result,runs.created FROM steps JOIN runs ON runs.id=steps.run WHERE steps.run=? AND steps.result IS NOT NULL",
                (row["item"], run),
            )
        for attempt in attempts:
            self._queue_log_policy(db, attempt["id"], attempt["runner"], deleted=True)
            db.execute("DELETE FROM runner_events WHERE attempt=?", (attempt["id"],))
        db.execute("DELETE FROM artifacts WHERE attempt IN (SELECT id FROM attempts WHERE run=?)", (run,))
        db.execute("DELETE FROM attempts WHERE run=?", (run,))
        db.execute("DELETE FROM steps WHERE run=?", (run,))
        db.execute("DELETE FROM events WHERE run=?", (run,))
        db.execute("DELETE FROM run_status_history WHERE run=?", (run,))
        db.execute("DELETE FROM affinity WHERE run=?", (run,))
        db.execute("DELETE FROM runs WHERE id=?", (run,))
        return paths

    def _prune_workflow(self, db, project, workflow):
        row = db.execute(
            "SELECT retention_count FROM workflow_retention WHERE project=? AND workflow=?", (project, workflow)
        ).fetchone()
        if row is None:
            return []
        keep = row["retention_count"]
        terminal = ("succeeded", "failed", "cancelled")
        placeholders = ",".join("?" for _ in terminal)
        candidates = db.execute(
            f"SELECT id FROM runs WHERE project=? AND workflow=? AND status IN ({placeholders}) AND keep_forever=0 ORDER BY created DESC,id DESC LIMIT -1 OFFSET ?",
            (project, workflow, *terminal, keep),
        ).fetchall()
        paths = []
        for candidate in candidates:
            paths.extend(self._delete_run_db(db, candidate["id"]))
        return paths

    def prune_runs(self):
        paths = []
        with self.transaction() as db:
            for row in db.execute("SELECT DISTINCT project,workflow FROM runs").fetchall():
                paths.extend(self._prune_workflow(db, row["project"], row["workflow"]))
        self._remove_artifact_files(paths)

    def pending_log_policy(self, runner):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM runner_log_policy WHERE runner=? AND acknowledged=0 ORDER BY generation LIMIT 1",
                (runner,),
            ).fetchone()
            return dict(row) if row else None

    def acknowledge_log_policy(self, runner, attempt, generation):
        with self.transaction() as db:
            db.execute(
                "DELETE FROM runner_log_policy WHERE runner=? AND attempt=? AND generation=?",
                (runner, attempt, generation),
            )
            run = db.execute(
                "SELECT runs.id FROM runs JOIN attempts ON attempts.run=runs.id WHERE attempts.id=?",
                (attempt,),
            ).fetchone()
            if run:
                pending = db.execute(
                    "SELECT 1 FROM runner_log_policy JOIN attempts ON attempts.id=runner_log_policy.attempt WHERE attempts.run=? AND runner_log_policy.acknowledged=0 LIMIT 1",
                    (run["id"],),
                ).fetchone()
                db.execute("UPDATE runs SET retention_pending=? WHERE id=?", (int(bool(pending)), run["id"]))

    def delete_run(self, run):
        """Delete terminal run history and stored artifacts, preserving workspaces."""
        with self.transaction() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run,)).fetchone()
            if row is None:
                raise StateConflict("Unknown run")
            if row["status"] in ACTIVE or db.execute(
                "SELECT 1 FROM attempts WHERE run=? AND status IN ('running','reconciling')",
                (run,),
            ).fetchone():
                raise StateConflict("Cancel and stop the active run before deleting it")
            if db.execute("SELECT keep_forever FROM runs WHERE id=?", (run,)).fetchone()[0]:
                raise StateConflict("Remove Keep forever before deleting this run")
            paths = self._delete_run_db(db, run)
        for path in paths:
            try:
                (self.artifact_root / path).unlink(missing_ok=True)
            except OSError:
                pass
        return {"deleted": run}

    def delete_project(self, project):
        """Delete a project's persisted history and artifacts, preserving workspaces."""
        with self.transaction() as db:
            exists = db.execute(
                "SELECT 1 FROM projects WHERE name=? UNION SELECT 1 FROM workflows WHERE project=? LIMIT 1",
                (project, project),
            ).fetchone()
            if exists is None:
                raise StateConflict("Unknown project")
            project_row = db.execute(
                "SELECT status FROM projects WHERE name=?", (project,)
            ).fetchone()
            if project_row and project_row["status"] == "refreshing":
                raise StateConflict("Wait for the project refresh to finish before deleting it")
            if db.execute(
                "SELECT 1 FROM runs WHERE project=? AND status IN ('queued','running','reconciling','waiting_approval') LIMIT 1",
                (project,),
            ).fetchone() or db.execute(
                "SELECT 1 FROM attempts JOIN runs ON runs.id=attempts.run WHERE runs.project=? AND attempts.status IN ('running','reconciling') LIMIT 1",
                (project,),
            ).fetchone():
                raise StateConflict("Cancel and stop active runs before deleting the project")

            runs = [row[0] for row in db.execute(
                "SELECT id FROM runs WHERE project=?", (project,)
            )]
            attempt_rows = db.execute(
                "SELECT attempts.id,attempts.runner FROM attempts JOIN runs ON runs.id=attempts.run WHERE runs.project=?",
                (project,),
            ).fetchall()
            attempts = [row["id"] for row in attempt_rows]
            paths = [row[0] for row in db.execute(
                "SELECT artifacts.path FROM artifacts JOIN attempts ON attempts.id=artifacts.attempt JOIN runs ON runs.id=attempts.run WHERE runs.project=?",
                (project,),
            )]

            item_ids = [row[0] for row in db.execute("SELECT id FROM items WHERE project=?", (project,))]
            for item in item_ids:
                db.execute("DELETE FROM item_history WHERE item=?", (item,))
                db.execute("DELETE FROM item_effects WHERE item=?", (item,))

            for attempt in attempt_rows:
                self._queue_log_policy(db, attempt["id"], attempt["runner"], deleted=True)

            if attempts:
                placeholders = ",".join("?" for _ in attempts)
                db.execute(
                    f"DELETE FROM runner_events WHERE attempt IN ({placeholders})",
                    attempts,
                )
                db.execute(
                    f"DELETE FROM artifacts WHERE attempt IN ({placeholders})",
                    attempts,
                )
                db.execute(
                    f"DELETE FROM attempts WHERE id IN ({placeholders})", attempts
                )
            if runs:
                placeholders = ",".join("?" for _ in runs)
                db.execute(f"DELETE FROM steps WHERE run IN ({placeholders})", runs)
                db.execute(f"DELETE FROM events WHERE run IN ({placeholders})", runs)
                db.execute(f"DELETE FROM run_status_history WHERE run IN ({placeholders})", runs)
                db.execute(f"DELETE FROM affinity WHERE run IN ({placeholders})", runs)
                db.execute(f"DELETE FROM effects WHERE run IN ({placeholders})", runs)
                db.execute(
                    f"DELETE FROM resource_locks WHERE run IN ({placeholders})", runs
                )
                db.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", runs)
            db.execute("DELETE FROM workflows WHERE project=?", (project,))
            db.execute("DELETE FROM workflow_retention WHERE project=?", (project,))
            db.execute("DELETE FROM items WHERE project=?", (project,))
            db.execute("DELETE FROM occurrences WHERE project=?", (project,))
            db.execute("DELETE FROM revisions WHERE project=?", (project,))
            db.execute("DELETE FROM project_organizations WHERE project=?", (project,))
            db.execute("DELETE FROM project_environment WHERE project=?", (project,))
            db.execute("DELETE FROM projects WHERE name=?", (project,))
            for runner in db.execute("SELECT id,projects FROM runners").fetchall():
                projects = [
                    name for name in json.loads(runner["projects"]) if name != project
                ]
                if len(projects) != len(json.loads(runner["projects"])):
                    db.execute(
                        "UPDATE runners SET projects=? WHERE id=?",
                        (json.dumps(projects), runner["id"]),
                    )

        for path in paths:
            try:
                (self.artifact_root / path).unlink(missing_ok=True)
            except OSError:
                pass
        return {"deleted": project}

    def events(self, after=0):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE id>? ORDER BY id", (after,)
                )
            ]
