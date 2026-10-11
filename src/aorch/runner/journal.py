"""Crash-resistant assignments and pending results; no engine token is stored."""

import hmac
import json
import os
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Journal:
    def __init__(self, path, secrets_key=None):
        self.path = path
        self.secrets_key = (
            hmac.digest(secrets_key.encode(), b"Runestra runner journal secrets v1", "sha256")
            if secrets_key else None
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS resources (attempt TEXT, name TEXT, platform TEXT, identifier TEXT, pid INTEGER, process_identity TEXT, active INTEGER, PRIMARY KEY(attempt,name))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries (attempt TEXT PRIMARY KEY, assignment TEXT, state TEXT, pid INTEGER, process_identity TEXT, result TEXT)"
            )

    def save(self, assignment):
        stored = dict(assignment)
        secret_names = assignment.get("project_secret_names", [])
        if secret_names:
            if self.secrets_key is None:
                raise ValueError("Runner journal encryption is not configured")
            environment = dict(assignment.get("project_environment", {}))
            secret_values = {name: environment.pop(name) for name in secret_names if name in environment}
            nonce = os.urandom(12)
            stored["project_environment"] = environment
            stored["project_environment_ciphertext"] = (
                nonce + AESGCM(self.secrets_key).encrypt(
                    nonce, json.dumps(secret_values, sort_keys=True).encode(), assignment["attempt_id"].encode()
                )
            ).hex()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO entries VALUES(?,?,'accepted',NULL,NULL,NULL)",
                (assignment["attempt_id"], json.dumps(stored)),
            )

    def started(self, attempt, pid, identity):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE entries SET state='running',pid=?,process_identity=? WHERE attempt=?",
                (pid, identity, attempt),
            )

    def completed(self, attempt, result):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE entries SET state='completed',result=? WHERE attempt=?",
                (json.dumps(result), attempt),
            )

    def delivered(self, attempt):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE entries SET state='delivered' WHERE attempt=?", (attempt,)
            )

    def pending(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            entries = [dict(row) for row in db.execute("SELECT * FROM entries WHERE state<>'delivered'")]
        for entry in entries:
            assignment = json.loads(entry["assignment"])
            encrypted = assignment.pop("project_environment_ciphertext", None)
            if encrypted:
                if self.secrets_key is None:
                    raise ValueError("Runner journal encryption key is unavailable")
                payload = bytes.fromhex(encrypted)
                values = json.loads(AESGCM(self.secrets_key).decrypt(
                    payload[:12], payload[12:], assignment["attempt_id"].encode()
                ))
                assignment.setdefault("project_environment", {}).update(values)
            entry["assignment"] = json.dumps(assignment)
        return entries

    def resource_started(
        self, attempt, name, platform, identifier, pid=None, identity=None
    ):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO resources VALUES(?,?,?,?,?,?,1)",
                (attempt, name, platform, identifier, pid, identity),
            )

    def resource_stopped(self, attempt, name):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE resources SET active=0 WHERE attempt=? AND name=?",
                (attempt, name),
            )

    def active_resources(self, attempt):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM resources WHERE attempt=? AND active=1", (attempt,)
                )
            ]
