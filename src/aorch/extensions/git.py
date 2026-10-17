"""Isolated Git workspaces, verification and reconcilable publication actions."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from aorch.contracts import Artifact, StepResult
from aorch.extensions.ledger import EffectLedger
from aorch.runner.executor import environment, process_identity
from aorch.runner.process import log_context, run_process


async def command(argv, cwd, config, journal, assignment, cancelled, timeout=300):
    code, stdout, stderr = await run_process(
        argv,
        cwd,
        environment(config),
        "",
        timeout,
        cancelled,
        lambda pid: journal.started(
            assignment["attempt_id"], pid, process_identity(pid)
        ),
    )
    if code:
        raise ValueError(f"Command failed ({code}): {stderr[:4000]}")
    return stdout.strip()


async def git_action(
    name, inputs, context, assignment, config, journal, cancelled, workspace
):
    code = workspace / "code"
    metadata = workspace / "workspace.json"
    ledger = EffectLedger(config.workspace_root / "effects.sqlite")

    async def run(argv, cwd=code, timeout=300):
        return await command(argv, cwd, config, journal, assignment, cancelled, timeout)

    if name == "git.branch":
        repository = assignment["package"].get("repository", {}).get("git")
        if not repository:
            repository = await run(["git", "remote", "get-url", "origin"])
        run_id = assignment["run_id"]
        owner = assignment.get("workspace_key", run_id)
        slug = re.sub(r"[^A-Za-z0-9_.-]", "-", owner)[:40]
        branch = f"codex/{slug}-{hashlib.sha256(owner.encode()).hexdigest()[:10]}"
        if metadata.exists():
            saved = json.loads(metadata.read_text())
            if saved["repository"] != repository or saved["branch"] != branch:
                raise ValueError("Workspace identity mismatch")
            if saved.get("mode") != "checkout":
                raise ValueError("Workspace was prepared with an incompatible mode")
            if saved.get("owner", owner) != owner:
                raise ValueError("Workspace belongs to another workspace identity")
            if not code.exists():
                raise ValueError("Workspace metadata exists but checkout is missing")
            current_branch = await run(["git", "branch", "--show-current"])
            if current_branch != branch:
                observed = current_branch or "detached HEAD"
                raise ValueError(
                    f"Workspace for {repository} is on '{observed}', but its saved "
                    f"workflow branch is '{branch}'. Restore the saved branch or "
                    "inspect workspace history before retrying."
                )
            if await run(["git", "rev-parse", "HEAD"]) != saved["starting_commit"]:
                raise ValueError("Workspace commit changed after branch preparation")
            return StepResult(
                status="succeeded",
                summary="Existing branch retained",
                outputs=saved,
                workspace_revision=await run(["git", "rev-parse", "HEAD"]),
            )
        if await run(["git", "status", "--porcelain"]):
            raise ValueError("Refusing to create a branch in a dirty checkout")
        starting = await run(["git", "rev-parse", "HEAD"])
        current = await run(["git", "branch", "--show-current"])
        if current != branch:
            await run(["git", "switch", "-c", branch])
        saved = {
            "repository": repository,
            "branch": branch,
            "starting_commit": starting,
            "mode": "checkout",
            "owner": owner,
        }
        metadata.write_text(json.dumps(saved))
        return StepResult(
            status="succeeded",
            summary="Run branch prepared",
            outputs=saved,
            workspace_revision=starting,
        )

    if name == "git.prepare":
        repository = config.repositories[inputs["repository"]].resolve()
        branch_key = inputs.get("item_id", assignment["run_id"])
        slug = re.sub(r"[^A-Za-z0-9_.-]", "-", branch_key)[:60]
        branch = f"codex/{slug}-{hashlib.sha256(branch_key.encode()).hexdigest()[:10]}"
        if metadata.exists():
            saved = json.loads(metadata.read_text())
            if saved["repository"] != str(repository) or saved["branch"] != branch:
                raise ValueError("Workspace identity mismatch")
            owner = assignment.get("workspace_key", assignment["run_id"])
            if saved.get("owner", owner) != owner:
                raise ValueError("Workspace belongs to another workspace identity")
            if not code.exists():
                raise ValueError("Workspace metadata exists but worktree is missing")
            return StepResult(
                status="succeeded",
                summary="Existing workspace retained",
                outputs={
                    **saved,
                    "mode": saved.get("mode", "worktree"),
                    "owner": saved.get("owner", owner),
                },
                workspace_revision=await run(["git", "rev-parse", "HEAD"]),
            )
        if code.exists():
            raise ValueError("Untracked workspace directory already exists")
        base = inputs.get("base", "HEAD")
        starting = await run(
            ["git", "rev-parse", "--verify", f"{base}^{{commit}}"], repository
        )
        existing = await run(
            ["git", "for-each-ref", f"refs/heads/{branch}", "--format=%(refname)"],
            repository,
        )
        if existing:
            return StepResult(
                status="needs_attention",
                summary="Ticket branch already exists; explicit workspace recovery required",
            )
        await run(
            ["git", "worktree", "add", "-b", branch, str(code), starting], repository
        )
        saved = {
            "repository": str(repository),
            "branch": branch,
            "starting_commit": starting,
            "mode": "worktree",
            "owner": assignment.get("workspace_key", assignment["run_id"]),
        }
        metadata.write_text(json.dumps(saved))
        return StepResult(
            status="succeeded",
            summary="Isolated worktree prepared",
            outputs=saved,
            workspace_revision=starting,
        )
    if not metadata.exists():
        raise ValueError("Git workspace must be prepared first")
    saved = json.loads(metadata.read_text())

    async def fingerprint():
        # Hash tracked and untracked content rather than trusting mtimes or HEAD.
        listing = await run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        digest = hashlib.sha256()
        digest.update((await run(["git", "write-tree"])).encode())
        for relative in sorted(set(listing.split("\0")) - {""}):
            path = code / relative
            digest.update(relative.encode())
            if path.is_symlink():
                digest.update(str(path.readlink()).encode())
            elif path.is_file():
                digest.update(path.read_bytes())
                digest.update(str(path.stat().st_mode & 0o111).encode())
            else:
                digest.update(b"deleted")
        return digest.hexdigest()

    if name == "coding.repair":
        from aorch.config.models import Step
        from aorch.runner.agents import execute_agent

        limit = inputs.get("max_iterations", 3)
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("Repair iterations must be between 1 and 20")

        def repair_log(message, **details):
            logger = log_context.get()
            if logger:
                logger("repair", {"message": message, **details})

        last_outcome = "No review or test result was recorded"
        for iteration in range(limit):
            iteration_number = iteration + 1
            repair_log(
                f"Repair iteration {iteration_number}/{limit} started",
                iteration=iteration_number,
                iteration_limit=limit,
            )
            if cancelled.is_set():
                return StepResult(status="cancelled", summary="Repair stopped")
            for role in ("implement", "review"):
                repair_log(
                    f"{role.capitalize()} started in repair iteration {iteration_number}/{limit}",
                    iteration=iteration_number,
                    role=role,
                )
                definition = Step(
                    type="agent",
                    profile=inputs[f"{role}_profile"],
                    prompt=inputs[f"{role}_prompt"],
                    skills=inputs.get("skills", []),
                    timeout_seconds=assignment["step"]["timeout_seconds"],
                )
                child = {**assignment, "step": definition.model_dump(mode="json")}
                agent_context = dict(context)
                if role == "review":
                    agent_context["coding_repair_role"] = "review"
                result = await execute_agent(
                    child,
                    config,
                    journal,
                    cancelled,
                    code,
                    workspace / ".aorch-package",
                    agent_context,
                )
                context[role] = result.model_dump(mode="json")
                role_summary = " ".join(result.summary.split())[:500]
                repair_log(
                    f"{role.capitalize()} finished with {result.status}: {role_summary}",
                    iteration=iteration_number,
                    role=role,
                    status=result.status,
                )
                if result.status != "succeeded":
                    result.summary = (
                        f"{role.capitalize()} failed in repair iteration "
                        f"{iteration_number}/{limit}: {role_summary}"
                    )
                    return result
            if context["review"]["outputs"].get("approved") is not True:
                approval = context["review"]["outputs"].get("approved")
                last_outcome = (
                    f"Review did not set outputs.approved to true (received {approval!r}). "
                    + " ".join(context["review"]["summary"].split())[:500]
                )
                context["review_feedback"] = last_outcome
                repair_log(
                    f"Repair iteration {iteration_number}/{limit} not approved: {last_outcome}",
                    iteration=iteration_number,
                    approved=False,
                )
                continue
            repair_log(
                f"Review approved repair iteration {iteration_number}/{limit}; running checks",
                iteration=iteration_number,
                approved=True,
            )
            passed = True
            before = await fingerprint()
            for argv in inputs["test_commands"]:
                command_text = " ".join(argv)
                repair_log(
                    f"Running verification command: {command_text}",
                    iteration=iteration_number,
                    command=command_text,
                )
                try:
                    await run(argv)
                except ValueError as error:
                    failure = " ".join(str(error).split())[:1000]
                    context["test_failure"] = failure
                    last_outcome = f"Verification command '{command_text}' failed: {failure}"
                    repair_log(
                        last_outcome,
                        iteration=iteration_number,
                        command=command_text,
                        status="failed",
                    )
                    passed = False
                    break
                repair_log(
                    f"Verification command passed: {command_text}",
                    iteration=iteration_number,
                    command=command_text,
                    status="passed",
                )
            if passed and before == await fingerprint():
                (workspace / "verified.json").write_text(
                    json.dumps(
                        {
                            "fingerprint": before,
                            "head": await run(["git", "rev-parse", "HEAD"]),
                        }
                    )
                )
                diff = await run(["git", "diff", "--binary", "HEAD"])
                untracked = await run(
                    ["git", "ls-files", "--others", "--exclude-standard", "-z"]
                )
                for relative in filter(None, untracked.split("\0")):
                    path = (code / relative).resolve()
                    if not path.is_relative_to(code.resolve()):
                        raise ValueError("Untracked path escapes the workspace")
                    if path.is_symlink():
                        content = f"Symlink target: {path.readlink()}"
                    elif path.is_file():
                        try:
                            content = path.read_text()
                        except UnicodeDecodeError:
                            content = f"Binary file, {path.stat().st_size} bytes"
                    else:
                        continue
                    diff += f"\n--- New file: {relative} ---\n{content}\n"
                    if len(diff.encode()) > 5 * 1024 * 1024:
                        raise ValueError("Approval diff exceeds the 5 MiB review limit")
                diff_path = workspace / "approval.diff"
                diff_path.write_text(diff)
                diff_bytes = diff_path.read_bytes()
                return StepResult(
                    status="succeeded",
                    summary=f"Review and tests passed after {iteration + 1} iteration(s)",
                    outputs={"verified": True, "fingerprint": before},
                    artifacts=[Artifact(
                        path="approval.diff",
                        sha256=hashlib.sha256(diff_bytes).hexdigest(),
                        size=len(diff_bytes),
                    )],
                    workspace_revision=await run(["git", "rev-parse", "HEAD"]),
                )
            repair_log(
                f"Repair iteration {iteration_number}/{limit} complete; starting another iteration",
                iteration=iteration_number,
                outcome=last_outcome,
            )
        summary = (
            f"Repair iteration limit exhausted after {limit} iteration(s). "
            f"Last outcome: {last_outcome}"
        )
        repair_log(summary, iteration_limit=limit, status="needs_attention")
        return StepResult(
            status="needs_attention", summary=summary
        )
    if name == "git.verify":
        commands = inputs["commands"]
        if not commands:
            raise ValueError("Verification requires test commands")
        paths = inputs.get("paths")
        if inputs.get("only_paths") and not paths:
            raise ValueError("Path-restricted verification requires paths")
        if paths:
            root = code.resolve()
            for reference in paths:
                relative = PurePosixPath(reference)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in reference
                    or not relative.parts
                ):
                    raise ValueError(f"Unsafe verification path: {reference}")
                if not (code / Path(*relative.parts)).resolve().is_relative_to(root):
                    raise ValueError(f"Verification path escapes workspace: {reference}")
                if not (code / Path(*relative.parts)).is_file():
                    raise ValueError(f"Verified file does not exist: {reference}")
        if paths and inputs.get("only_paths"):
            tracked = set(
                filter(None, (await run(["git", "diff", "--name-only", "HEAD", "-z"])).split("\0"))
            )
            untracked = set(
                filter(
                    None,
                    (await run(["git", "ls-files", "--others", "--exclude-standard", "-z"])).split("\0"),
                )
            )
            unexpected = (tracked | untracked) - set(paths)
            if unexpected:
                raise ValueError(
                    f"Workspace contains changes outside the declared paths: {sorted(unexpected)}"
                )
        before = await fingerprint()
        diagnostics = []
        artifacts = []
        for command_index, argv in enumerate(commands):
            output = await run(argv, timeout=assignment["step"]["timeout_seconds"])
            try:
                structured = json.loads(output)
            except json.JSONDecodeError:
                structured = None
            if not isinstance(structured, dict) or structured.get("status") not in {
                "succeeded", "failed", "needs_attention"
            }:
                continue
            diagnostics.append({
                "summary": structured.get("summary", ""),
                **structured.get("outputs", {}),
            })
            for item in structured.get("artifacts", []):
                relative = PurePosixPath(item.get("path", ""))
                path = code / Path(*relative.parts)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not relative.parts
                    or not path.resolve().is_relative_to(code.resolve())
                    or not path.is_file()
                ):
                    raise ValueError("Verification returned an invalid diagnostic artifact path")
                data = path.read_bytes()
                if len(data) != item.get("size") or hashlib.sha256(data).hexdigest() != item.get("sha256"):
                    raise ValueError("Verification diagnostic artifact changed before upload")
                artifacts.append(
                    Artifact.model_validate({
                        **item,
                        "path": (Path("code") / Path(*relative.parts)).as_posix(),
                    })
                )
            if structured["status"] != "succeeded":
                outputs = dict(structured.get("outputs", {}))
                outputs["verification_commands_not_run"] = commands[command_index + 1 :]
                return StepResult(
                    status=structured["status"],
                    summary=structured.get("summary", "Verification failed"),
                    error=structured.get("error", "VerificationFailure"),
                    outputs=outputs,
                    artifacts=artifacts,
                )
        if before != await fingerprint():
            raise ValueError(
                "Tests changed workspace content; verification invalidated"
            )
        revision = await run(["git", "rev-parse", "HEAD"])
        (workspace / "verified.json").write_text(
            json.dumps({"fingerprint": before, "head": revision})
        )
        return StepResult(
            status="succeeded",
            summary="; ".join(
                str(item.get("summary", "Verification passed"))
                for item in diagnostics
            ) or "Tests passed",
            outputs={"verified": True, "fingerprint": before},
            artifacts=artifacts,
            workspace_revision=revision,
        )
    verification = json.loads((workspace / "verified.json").read_text())
    current_fingerprint = await fingerprint()
    if verification["fingerprint"] != current_fingerprint:
        raise ValueError("Workspace changed after verification")
    if name == "git.commit":
        approval_fingerprint = inputs.get("approval_fingerprint")
        if approval_fingerprint is not None and approval_fingerprint != current_fingerprint:
            raise ValueError("Approval does not match the verified workspace content")
        commit_file = workspace / "commit.json"
        if commit_file.exists():
            recorded = json.loads(commit_file.read_text())["commit"]
            if recorded != await run(["git", "rev-parse", "HEAD"]):
                raise ValueError("HEAD changed after recorded commit")
            return StepResult(
                status="succeeded",
                summary="Existing commit retained",
                outputs={"commit": recorded, "branch": saved["branch"]},
                workspace_revision=recorded,
            )
        marker = f"aorch-run: {assignment['run_id']}"
        head_message = await run(["git", "log", "-1", "--format=%B"])
        if marker not in head_message:
            paths = inputs.get("paths")
            if paths:
                root = code.resolve()
                safe_paths = []
                for reference in paths:
                    relative = PurePosixPath(reference)
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or "\\" in reference
                        or not relative.parts
                    ):
                        raise ValueError(f"Unsafe commit path: {reference}")
                    target = (code / Path(*relative.parts)).resolve()
                    if not target.is_relative_to(root):
                        raise ValueError(f"Commit path escapes workspace: {reference}")
                    safe_paths.append(relative.as_posix())
                await run(["git", "add", "--all", "--", *safe_paths])
            else:
                await run(["git", "add", "--all"])
            staged = set(
                filter(
                    None,
                    (await run(["git", "diff", "--cached", "--name-only", "-z"])).split("\0"),
                )
            )
            if paths and staged - set(safe_paths):
                raise ValueError("Refusing to commit unrelated staged files")
            if not staged:
                raise ValueError("No staged changes to commit")
            await run(["git", "commit", "-m", inputs["message"] + "\n\n" + marker])
        revision = await run(["git", "rev-parse", "HEAD"])
        commit_file.write_text(json.dumps({"commit": revision}))
        (workspace / "verified.json").write_text(
            json.dumps({"fingerprint": await fingerprint(), "head": revision})
        )
        return StepResult(
            status="succeeded",
            summary="Verified content committed",
            outputs={"commit": revision, "branch": saved["branch"]},
            workspace_revision=revision,
        )
    revision = json.loads((workspace / "commit.json").read_text())["commit"]
    if revision != await run(["git", "rev-parse", "HEAD"]):
        raise ValueError("Publication HEAD differs from verified commit")
    remote = inputs.get("remote", "origin")
    if name == "git.push":
        refs = await run(["git", "ls-remote", remote, f"refs/heads/{saved['branch']}"])
        if refs and refs.split()[0] != revision:
            return StepResult(
                status="needs_attention",
                summary="Remote ticket branch differs; refusing overwrite",
            )
        if not refs:
            await run(
                ["git", "push", remote, f"{revision}:refs/heads/{saved['branch']}"]
            )
        confirmed = await run(
            ["git", "ls-remote", remote, f"refs/heads/{saved['branch']}"]
        )
        if not confirmed or confirmed.split()[0] != revision:
            raise ValueError("Remote commit verification failed")
        return StepResult(
            status="succeeded",
            summary="Verified commit pushed",
            outputs={"remote": remote, "branch": saved["branch"], "commit": revision},
            workspace_revision=revision,
        )
    if name in {"git.pr", "github.open_pr"}:
        repository = inputs["repository"]
        branch = saved["branch"]
        marker = (
            f"[aorch:pr:{hashlib.sha256((repository + branch).encode()).hexdigest()}]"
        )
        refs = await run(["git", "ls-remote", remote, f"refs/heads/{branch}"])
        if not refs or refs.split()[0] != revision:
            raise ValueError("PR requires the verified commit on the remote branch")
        existing = json.loads(
            await run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repository,
                    "--head",
                    branch,
                    "--state",
                    "all",
                    "--json",
                    "url,number,body,headRefOid,baseRefName,state",
                ]
            )
        )
        found = next((pr for pr in existing if marker in pr["body"]), None)
        if found and found["headRefOid"] != revision:
            raise ValueError("Existing PR commit differs from verified commit")
        if found and found["baseRefName"] != inputs["base"]:
            raise ValueError("Existing PR targets a different base branch")
        if found and found["state"] != "OPEN":
            return StepResult(
                status="needs_attention",
                summary="The existing pull request is no longer open",
            )
        effect = ledger.get(marker)
        if not found:
            if existing or effect:
                return StepResult(
                    status="needs_attention",
                    summary="PR outcome or ownership uncertain; reconcile before retry",
                )
            ledger.intent(marker)
            body_file = workspace / "pr-body.md"
            body_file.write_text(inputs["body"] + "\n\n" + marker)
            await run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    repository,
                    "--head",
                    branch,
                    "--base",
                    inputs["base"],
                    "--title",
                    inputs["title"],
                    "--body-file",
                    str(body_file),
                ]
            )
            found = json.loads(
                await run(
                    [
                        "gh",
                        "pr",
                        "view",
                        branch,
                        "--repo",
                        repository,
                        "--json",
                        "url,number,body,headRefOid,baseRefName,state",
                    ]
                )
            )
            if found["headRefOid"] != revision:
                raise ValueError("Created PR commit differs from verified commit")
            if found["baseRefName"] != inputs["base"] or found["state"] != "OPEN":
                raise ValueError("Created pull request has the wrong base branch or state")
        ledger.intent(marker)
        ledger.done(marker, {"provider_id": found["url"]})
        return StepResult(
            status="succeeded",
            summary="PR verified",
            outputs={
                "pull_request": found["url"],
                "number": found["number"],
                "branch": branch,
                "commit": revision,
                "head_commit": found["headRefOid"],
                "base": found["baseRefName"],
                "external_effects": [
                    {"key": marker, "kind": "git.pr", "provider_id": found["url"]}
                ]
            },
            workspace_revision=revision,
        )
    raise ValueError(f"Unsupported Git action: {name}")
