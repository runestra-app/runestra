# Runestra

Run workflows with AI agents, shell commands and Git actions. An engine keeps
state and serves the browser; runners execute the work on your machines.

## Quick start

On macOS or Linux, copy this into a terminal. Requires Git and Bash; the script
installs `uv` when needed, and `uv` supplies Python and the project dependencies.

```sh
git clone https://github.com/runestra-app/runestra.git
cd runestra
bash scripts/quick-start.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), click **Continue locally**,
then select **local-actions → commands**. The script starts both processes and
runs the sample automatically. The terminal prints **Sample succeeded** when
both steps finish, and the browser shows the same result.

No Docker, email service, AI account or API key is needed for this first run.
Keep the terminal open. **Ctrl+C** stops both processes and keeps your data.
Restart with `bash scripts/quick-start.sh`; it also runs a fresh sample.

Already cloned the repository? Run only `bash scripts/quick-start.sh` from its
root. If port 8765 is occupied, use `bash scripts/quick-start.sh --port 8875` and
open the address printed by the script.

This tries the current source on `main`. Clean installations on both target
platforms and a tagged public release still need acceptance checks.

## Try an AI workflow

Install and sign in to either Codex CLI or Claude Code on the runner machine.
The [first AI workflow guide](docs/getting-started.md#try-an-ai-workflow) shows a
small read-only example you can copy into your project. The
[maintained examples](examples/public-mvp/README.md) also cover manual change
requests and GitHub or Jira issue discovery with approval before publication.

## Documentation

| I want to… | Read |
|---|---|
| Try the sample, restart it, or add an AI step | [Getting started](docs/getting-started.md) |
| Write a workflow or give the task to an agent | [Workflow guide](docs/workflows.md) |
| Configure imports, inputs, schedules, connections and secrets | [Configuration](docs/configuration.md) |
| Choose an agent profile or a built-in action | [Profiles and actions](docs/profiles-and-actions.md) |
| Install a release or run on a Linux server | [Deployment](docs/deployment.md) |
| Use email login, remote runners, backups or recovery | [Operations](docs/operations.md) |
| Understand security and trust boundaries | [Security](SECURITY.md) |

The [documentation index](docs/README.md) lists user guides and the YAML
reference. Development material is kept separately from the user guides.

## License

[Apache-2.0](LICENSE). Third-party dependency licenses and notices are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
