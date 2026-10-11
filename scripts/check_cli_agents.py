"""Opt-in live verification using the runner user's existing CLI logins."""

import asyncio
import json
import tempfile
from pathlib import Path

from aorch.config import snapshot
from aorch.config.service import RunnerConfig
from aorch.runner.executor import execute
from aorch.runner.journal import Journal


async def main():
    root = Path(tempfile.mkdtemp(prefix="aorch-cli-check-"))
    project = root / "project"
    configuration = project / ".aorch"
    skill = configuration / "skills/read"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: read-fixture\ndescription: Read a fixture and report its content.\n---\nRead fixture.txt using a file tool. Return outputs_json containing the exact fixture content under key content."
    )
    (configuration / "prompt.md").write_text(
        "Apply the read-fixture skill. The file fixture.txt is in the workspace. Do not modify any files."
    )
    config = RunnerConfig(
        id="manual",
        token_file=root / "unused",
        workspace_root=root / "workspaces",
        journal=root / "journal.sqlite",
    )
    journal = Journal(config.journal)
    for adapter in ("codex", "claude"):
        (configuration / "config.yaml").write_text(f"""version: 1
name: cli-check
profiles:
  reader:
    adapter: {adapter}
    authentication: local
    tools: [read]
    sandbox: read-only
workflows:
  read:
    steps:
      read:
        type: agent
        profile: reader
        prompt: prompt.md
        skills: [skills/read]
        timeout_seconds: 90
""")
        package = snapshot(project)
        workspace = config.workspace_root / adapter
        workspace.mkdir(parents=True)
        (workspace / "fixture.txt").write_text("aorch live fixture")
        assignment = {
            "attempt_id": adapter,
            "run_id": adapter,
            "step": package["configuration"]["workflows"]["read"]["steps"]["read"],
            "inputs": {},
            "package": package,
        }
        journal.save(assignment)
        result = await execute(assignment, config, journal, asyncio.Event())
        print(
            json.dumps(
                {
                    "adapter": adapter,
                    "status": result.status,
                    "summary": result.summary,
                    "fixture_matches": result.outputs.get("content", "").strip()
                    == "aorch live fixture",
                }
            ),
            flush=True,
        )
        (root / f"{adapter}-result.json").write_text(result.model_dump_json(indent=2))
    print(f"Evidence directory: {root}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
