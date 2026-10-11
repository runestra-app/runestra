"""Private, bounded, temporary runner-side execution logs."""

import json
import os
import re
import time
from pathlib import Path


class LogStore:
    def __init__(self, root, retention_days=7, max_total_bytes=2 * 1024**3, max_attempt_bytes=50 * 1024**2):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.retention_seconds = retention_days * 86400
        self.max_total_bytes = max_total_bytes
        self.max_attempt_bytes = max_attempt_bytes
        self._used_bytes = self._scan_total_bytes()

    @staticmethod
    def _safe(value):
        return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:128]

    def _directory(self, attempt):
        path = self.root / self._safe(attempt)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def append(self, attempt, event):
        directory = self._directory(attempt)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
            "attempt_id": attempt, "sequence": 0, "bytes": 0, "truncated": False,
            "created": time.time(), "completed": None,
        }
        if manifest["truncated"]:
            return False
        encoded = (json.dumps({"sequence": manifest["sequence"] + 1, "time": time.time(), **event}, ensure_ascii=False) + "\n").encode()
        if manifest["bytes"] + len(encoded) > self.max_attempt_bytes:
            manifest["truncated"] = True
            self._write_manifest(manifest_path, manifest)
            return False
        self._make_room(len(encoded), directory)
        if self._total_bytes() + len(encoded) > self.max_total_bytes:
            manifest["truncated"] = True
            self._write_manifest(manifest_path, manifest)
            return False
        event_path = directory / "events.jsonl"
        offset = event_path.stat().st_size if event_path.exists() else 0
        if manifest["sequence"] % 100 == 0:
            manifest.setdefault("offsets", []).append([manifest["sequence"] + 1, offset])
        with event_path.open("ab") as stream:
            stream.write(encoded)
        os.chmod(event_path, 0o600)
        manifest["sequence"] += 1
        manifest["bytes"] += len(encoded)
        self._used_bytes += len(encoded)
        self._write_manifest(manifest_path, manifest)
        return True

    @staticmethod
    def _write_manifest(path, data):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data))
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def complete(self, attempt):
        directory = self._directory(attempt)
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {
            "attempt_id": attempt, "sequence": 0, "bytes": 0, "truncated": False, "created": time.time(),
        }
        manifest["completed"] = time.time()
        self._write_manifest(path, manifest)
        return {key: manifest[key] for key in ("sequence", "bytes", "truncated", "completed")}

    def delete(self, attempt):
        directory = self.root / self._safe(attempt)
        if directory.is_dir():
            self._delete_attempt(directory)

    def set_retained(self, attempt, retained):
        directory = self._directory(attempt)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
            "attempt_id": attempt, "sequence": 0, "bytes": 0, "truncated": False,
            "created": time.time(), "completed": None,
        }
        manifest["retained"] = bool(retained)
        self._write_manifest(manifest_path, manifest)

    def read(self, attempt, after=0, limit=500, view="activity"):
        directory = self.root / self._safe(attempt)
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            return {"available": False, "events": [], "next": after, "truncated": False}
        manifest = json.loads(manifest_path.read_text())
        events_path = directory / "events.jsonl"
        events = []
        if events_path.exists():
            offsets = [entry for entry in manifest.get("offsets", []) if entry[0] <= after + 1]
            start_offset = offsets[-1][1] if offsets else 0
            with events_path.open() as stream:
                stream.seek(start_offset)
                for line in stream:
                    event = json.loads(line)
                    if event["sequence"] > after:
                        if view == "activity" and event.get("kind") == "stream":
                            event["message"] = f"[{event.get('stream')}] {event.get('message', '')}"
                        events.append(event)
                        if len(events) >= min(max(limit, 1), 16):
                            break
        next_cursor = events[-1]["sequence"] if events else after
        return {"available": True, "events": events, "next": next_cursor, "truncated": manifest["truncated"], "complete": manifest["completed"] is not None}

    def prune(self):
        cutoff = time.time() - self.retention_seconds
        for path in self.root.iterdir():
            manifest = path / "manifest.json"
            if path.is_dir() and manifest.exists():
                try:
                    data = json.loads(manifest.read_text())
                    if not data.get("retained") and data.get("completed") and data["completed"] < cutoff:
                        self._delete_attempt(path)
                except (OSError, ValueError):
                    continue

    def _total_bytes(self):
        return self._used_bytes

    def _scan_total_bytes(self):
        total = 0
        for path in self.root.glob("*/events.jsonl"):
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def _make_room(self, required, current):
        candidates = []
        for path in self.root.iterdir():
            manifest_path = path / "manifest.json"
            if path == current or not path.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                if manifest.get("completed") and not manifest.get("retained"):
                    candidates.append((manifest["completed"], path))
            except (OSError, ValueError):
                continue
        for _, path in sorted(candidates):
            if self._total_bytes() + required <= self.max_total_bytes:
                break
            self._delete_attempt(path)

    def _delete_attempt(self, path):
        event_path = path / "events.jsonl"
        if event_path.exists():
            self._used_bytes = max(0, self._used_bytes - event_path.stat().st_size)
        for child in path.iterdir():
            child.unlink(missing_ok=True)
        path.rmdir()
