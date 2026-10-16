"""Attempt-local temporary storage and sandbox denial diagnostics."""

import os
import re
from pathlib import Path

SCRATCH_ENVIRONMENT = {
    "TMPDIR": "tmp",
    "TMP": "tmp",
    "TEMP": "tmp",
    "XDG_CACHE_HOME": "cache",
    "PIP_CACHE_DIR": "cache/pip",
    "PYTHONPYCACHEPREFIX": "cache/python",
    "CARGO_HOME": "cache/cargo",
    "CARGO_TARGET_DIR": "build/cargo",
    "GOCACHE": "cache/go-build",
    "GOMODCACHE": "cache/go-mod",
    "npm_config_cache": "cache/npm",
    "YARN_CACHE_FOLDER": "cache/yarn",
    "PNPM_STORE_DIR": "cache/pnpm",
    "GRADLE_USER_HOME": "cache/gradle",
    "CCACHE_DIR": "cache/ccache",
    "SCCACHE_DIR": "cache/sccache",
    "CLANG_MODULE_CACHE_PATH": "cache/clang-modules",
    "SWIFT_MODULE_CACHE_PATH": "cache/swift-modules",
    "NUGET_PACKAGES": "cache/nuget",
    "DOTNET_CLI_HOME": "cache/dotnet",
    "GEM_HOME": "cache/gems",
    "BUNDLE_PATH": "cache/bundle",
    "PLAYWRIGHT_BROWSERS_PATH": "cache/playwright-browsers",
    "CYPRESS_CACHE_FOLDER": "cache/cypress",
}

_DENIAL = re.compile(
    r"(?:operation[ _]not[ _]permitted|permission denied|not permitted|read-only file system|sandbox.*(?:denied|blocked|violation)|(?:denied|blocked).*sandbox)",
    re.IGNORECASE,
)
_PATH = re.compile(r"(?:[/~][^\s:'\"<>]+)")
_QUOTED_PATH = re.compile(r"\\?['\"]((?:/|~)[^'\"]+)\\?['\"]")
_BROAD_ROOTS = {
    "/tmp", "/var/tmp", "/private/tmp", "/home", "/Users", "/Applications",
    "/etc", "/opt", "/usr", "/var",
}


def create_attempt_scratch(workspace, attempt_id):
    """Create a private scratch directory beside the repository checkout."""
    safe_attempt = re.sub(r"[^A-Za-z0-9_.-]", "_", str(attempt_id))[:128]
    if not safe_attempt:
        raise ValueError("Attempt ID cannot be empty")
    root = Path(workspace).resolve().parent / ".aorch-agent-scratch"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    scratch = root / safe_attempt
    scratch.mkdir(mode=0o700, exist_ok=False)
    os.chmod(scratch, 0o700)
    for relative in set(SCRATCH_ENVIRONMENT.values()):
        (scratch / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    return scratch


def scratch_environment(scratch):
    scratch = Path(scratch).resolve()
    return {
        "RUNESTRA_ATTEMPT_SCRATCH": str(scratch),
        **{name: str(scratch / relative) for name, relative in SCRATCH_ENVIRONMENT.items()},
    }


def validate_additional_writable_roots(configured, workspace):
    """Resolve explicit Codex roots while rejecting broad host locations."""
    home = Path.home().resolve()
    workspace = Path(workspace).resolve()
    roots = []
    for value in configured:
        path = Path(value)
        resolved = path.resolve(strict=False)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(f"Additional writable root must be an existing absolute directory: {value}")
        is_whole_home = any(
            resolved == base or (base in resolved.parents and len(resolved.relative_to(base).parts) == 1)
            for base in (Path("/home"), Path("/Users"))
        )
        if (
            str(resolved) in _BROAD_ROOTS
            or is_whole_home
            or resolved == home
            or resolved in home.parents
        ):
            raise ValueError(f"Additional writable root is too broad: {resolved}")
        if resolved in workspace.parents or resolved == workspace:
            raise ValueError(f"Additional writable root must not include the runner workspace: {resolved}")
        if resolved not in roots:
            roots.append(resolved)
    return roots


def sandbox_denial(stderr):
    """Return denial evidence from CLI diagnostics, retaining the exact line."""
    for line in reversed(stderr.splitlines()):
        if _DENIAL.search(line):
            paths = _QUOTED_PATH.findall(line) or _PATH.findall(line)
            if paths:
                paths[-1] = paths[-1].rstrip(".,;)]}")
            return {"message": line.strip()[:1200], "path": paths[-1] if paths else None}
    return None
