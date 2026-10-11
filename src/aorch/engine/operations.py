"""Single-engine guard and consistent portable backups."""

import fcntl
import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def engine_lock(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(database.suffix + ".engine.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another engine already owns this database") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def backup(store, artifacts: Path, destination: Path):
    if destination.exists():
        raise ValueError("Backup destination already exists")
    temporary = destination.with_name(destination.name + ".partial")
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        with store.transaction() as locked:
            with (
                sqlite3.connect(store.path) as source,
                sqlite3.connect(temporary / "engine.sqlite") as target,
            ):
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
            (temporary / "artifacts").mkdir()
            for row in locked.execute("SELECT path FROM artifacts"):
                shutil.copy2(artifacts / row[0], temporary / "artifacts" / row[0])
            (temporary / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": locked.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0]
                    }
                )
            )
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise


def prune_artifacts(store, artifacts: Path, before: float):
    """Prune artifacts and logs of old terminal runs, retaining assignment receipts."""
    with store.transaction() as db:
        rows = db.execute(
            "SELECT artifacts.id,artifacts.path FROM artifacts JOIN attempts ON artifacts.attempt=attempts.id JOIN runs ON attempts.run=runs.id WHERE runs.created<? AND runs.status NOT IN ('queued','running','reconciling','waiting_approval')",
            (before,),
        ).fetchall()
        for row in rows:
            (artifacts / row["path"]).unlink(missing_ok=True)
            db.execute("DELETE FROM artifacts WHERE id=?", (row["id"],))
        db.execute(
            "DELETE FROM runner_events WHERE attempt IN (SELECT attempts.id FROM attempts JOIN runs ON attempts.run=runs.id WHERE runs.created<? AND runs.status NOT IN ('queued','running','reconciling','waiting_approval'))",
            (before,),
        )
        db.execute(
            "DELETE FROM events WHERE run IN (SELECT id FROM runs WHERE created<? AND status NOT IN ('queued','running','reconciling','waiting_approval'))",
            (before,),
        )
    return len(rows)
