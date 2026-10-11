"""Verify content hashes and materialize text-only packages without traversal."""

import hashlib
import json
from pathlib import PurePosixPath

from aorch.config.models import ProjectConfig


def verify_package(package):
    payload = {key: value for key, value in package.items() if key != "sha256"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != package.get("sha256") or package.get("protocol_version") != "1":
        raise ValueError("Package digest or protocol mismatch")
    ProjectConfig.model_validate(package["configuration"])
    for name, file in package["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
            raise ValueError("Unsafe package path")
        if hashlib.sha256(file["content"].encode()).hexdigest() != file["sha256"]:
            raise ValueError("Package file digest mismatch")
    for name, file in package.get("config_sources", {}).items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
            raise ValueError("Unsafe configuration source path")
        if hashlib.sha256(file["content"].encode()).hexdigest() != file["sha256"]:
            raise ValueError("Configuration source digest mismatch")


def materialize(package, destination):
    verify_package(package)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    parent = destination.parent.resolve()
    if destination.is_symlink() or root == parent or not root.is_relative_to(parent):
        raise ValueError("Package destination must be a private directory inside its workspace")
    for name, file in package["files"].items():
        target = destination / name
        if not target.resolve().is_relative_to(root):
            raise ValueError("Package path escapes destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file["content"])
