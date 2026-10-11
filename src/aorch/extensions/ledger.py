"""Durable local intent ledger for external effects."""

import json
import sqlite3


class EffectLedger:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS effects (key TEXT PRIMARY KEY, status TEXT, result TEXT)"
            )

    def get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT status,result FROM effects WHERE key=?", (key,)
            ).fetchone()
            return (
                {"status": row[0], "result": json.loads(row[1]) if row[1] else None}
                if row
                else None
            )

    def intent(self, key):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO effects VALUES(?,'intent',NULL)", (key,))

    def done(self, key, result):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE effects SET status='done',result=? WHERE key=?",
                (json.dumps(result), key),
            )
