# Getting started

## Start the local sample

From an existing checkout:

```sh
bash scripts/quick-start.sh
```

The script installs `uv` if it is missing, resolves Python and locked dependencies,
creates private demo configuration under `.runtime/quick-start`, starts the engine
and runner, and queues the `commands` sample. It prints **Sample succeeded** after
the real runner completes both steps.

Open the printed URL, choose **Continue locally**, and select **local-actions →
commands**. Choose **Run workflow** to run it again. The sample also includes
`slow` to try cancellation and `failure` to inspect an intentional failure.

Press Ctrl+C to stop the demo. Run the same command to restart with existing
history. Each start queues a new `commands` run. An immediate restart can
wait up to 30 seconds for the previous runner registration to expire. Logs are in
`.runtime/quick-start/engine.log` and `.runtime/quick-start/runner.log`.

The helper explicitly enables loopback-only local sign-in. Ordinary deployments
use email sign-in; see [operations](operations.md#email-sign-in). The helper uses
its own demo directory, separate from an existing `.runtime/local` installation.

## If setup fails

| Symptom | Action |
|---|---|
| `git` is missing | Install Git using your OS package manager, then repeat the clone command |
| The port is occupied | Run `bash scripts/quick-start.sh --port 8875`; open the printed URL |
| Dependency download fails | Check network access and repeat the same command |
| The engine or runner exits | Read the log path printed by the script |
| The browser shows no projects | Sign in with **Continue locally** at the exact printed address |
| An AI step remains queued or fails | Install and sign in to the selected CLI on the runner machine |

For a separate demo, use `bash scripts/quick-start.sh --port 8875 --directory
.runtime/another-demo`. The directory must be empty or belong to this helper.

## Try an AI workflow

The shell sample needs no AI provider. To add one, install and sign in to Codex
CLI or Claude Code as the same OS user who runs the runner. A small diagnostic
request is optional and uses your provider account:

```sh
uv run --locked aorch agent-test codex
```

For Claude, replace `codex` with `claude`. The command asks before making a real
model request. Existing CLI login is used; Runestra does not create the provider
account or subscription.

In a project without an existing `.aorch/config.yaml`, copy this configuration:

```sh
mkdir -p .aorch
test ! -e .aorch/config.yaml && cat > .aorch/config.yaml <<'YAML'
version: 1
name: my-project
profiles:
  reviewer:
    adapter: codex
    authentication: local
    sandbox: read-only
    tools: [read]
workflows:
  review:
    steps:
      inspect:
        type: agent
        profile: reviewer
        prompt_text: |
          Read the repository and summarize its purpose.
          Point out up to three concrete maintenance concerns.
          Do not change files or run commands.
YAML
```

Use `adapter: claude` if that is your installed CLI. If `.aorch/config.yaml`
already exists, add the profile and workflow to that file instead of replacing
it. Run validation from the Runestra checkout:

```sh
uv run --locked aorch validate /absolute/path/to/your/project
```

Commit the configuration to your project's Git repository. In the Runestra
browser, add its Git URL and ref, then assign the local runner to it through
**System → Projects** on the runner row. Refresh the project, select **review**, and
choose **Run workflow**. Public repositories need no Git credentials; for a
private repository, configure the [project Git secrets](configuration.md).

This workflow uses inline prompts, which require a protocol 5 runner. The quick
start launches the engine and runner from the same checkout. It does not commit,
push or create a PR in the review workflow. Provider requests can include
repository content, according to the CLI's behavior.

## Continue with your own automation

Use the [workflow guide](workflows.md) for dependencies and output passing. The
[maintained examples](../examples/public-mvp/README.md) include manual changes,
GitHub Issues and Jira. Coding examples require project-specific tests, Git
credentials and explicit approval before publication.

For Docker, email authentication or a remote server, use
[deployment](deployment.md) and [operations](operations.md).
