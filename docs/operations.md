# Operations

## Docker Compose

The repository Compose file runs the engine on the host's loopback port
127.0.0.1:8765 and stores its database, artifacts, and configuration under
`.runtime/local`. The local installer runs the runner directly on the host so
it can use locally installed AI command line tools.

To run the example runner in a container instead, use the optional profile:

```sh
export RUNESTRA_UID="$(id -u)"
export RUNESTRA_GID="$(id -g)"
docker compose --profile container-runner up -d --build
```

The container runner has shell and built-in action support. It does not contain
Codex or Claude CLI installations or their login state. For AI workflows, keep
the runner on the host or configure the agent environment separately.

The local sign-in shortcut is enabled with `--local-auth`. It creates a local
owner on first sign-in and bypasses email verification. The installer enables
it only in its Compose override, which publishes the engine on 127.0.0.1. Do
not enable local sign-in for a public engine. Anyone using the computer can
sign in as that local owner. Use email sign-in behind HTTPS for a remote
deployment. The local shortcut is also available when starting the engine
directly:

```sh
uv run aorch engine .runtime/local/engine.yaml --local-auth
```

## Local demo

For the automated source demo, use [getting started](getting-started.md). It
uses `.runtime/quick-start`, queues a shell sample and supervises both processes.

## Email sign-in

Use email sign-in for a normal installation. Run these commands from the
repository root to create `.runtime/local` if it does not already exist:

```sh
uv sync --locked
uv run aorch init-local examples/local-actions --directory .runtime/local
```

Keep existing private configuration when upgrading. Do not run a second engine
against its database.

Edit `.runtime/local/auth-secrets.yaml` and replace `DUMMY_BREVO_API_KEY` with
your Brevo API key. Verify `no-reply@runestra.app` in Brevo. Then create the
initial owner. `--config` makes the command use the database configured for
this engine:

```sh
uv run aorch bootstrap-owner --email you@example.com --username you --organization your-org --config .runtime/local/engine.yaml
```

This reserves your email and assigns existing projects and runners to your
organization. Sign in with the same email address.

Start each process in its own terminal:

Engine terminal:

```sh
uv run --locked aorch engine .runtime/local/engine.yaml
```

Runner terminal:

```sh
uv run --locked aorch runner .runtime/local/runner.yaml
```

Open <http://127.0.0.1:8765>, enter your email, and use the link Brevo sends you.
The runner connects to the engine. Stop each process with Ctrl+C. Run only one
engine against a database.

Google and GitHub login remain hidden while their client credentials are dummy
values. To enable them, add the provider client ID to `engine.yaml`, the client
secret to `auth-secrets.yaml`, and register the callback URL shown in the
configuration examples.

The browser organizes data by project, workflow and run. A Git project refresh
fetches its configured ref and reads `.aorch/config.yaml`; it does not write to
the remote repository. Terminal runs can be deleted from the browser. Deletion
removes engine run events, attempts and stored artifacts. Online runners receive
a request to delete matching runner logs; offline runner logs expire under their
retention policy. Runner workspaces remain on disk. Cancel and confirm the stop
of an active run before deleting it.

To use installed agent CLIs, add their adapter names to `runner.yaml`. For API
authentication, configure `api_credentials` with provider to environment variable
mappings, export those variables for the runner process, and set the agent profile
to the same variable name. The runner injects API credentials only into the
selected API adapter. Shell, action and local-authentication agent subprocesses
do not inherit mapped credential variables. Jira connection metadata belongs
in the project configuration under `connections`, with `url`, optional `email`,
and a `token_env` reference. Store the token as a project secret with that exact
name. The built-in Jira action can read the project secret, while it remains
excluded from shell and agent subprocess environments. Never put token values
in YAML project or engine configuration.

## Remote runner

Expose the engine only through an HTTPS reverse proxy. Set the engine's external
URL in the runner's `.runtime/local/runner.yaml`. For example, if the engine is
reachable at `https://runestra.example.com`, set:

```yaml
engine_url: "https://runestra.example.com"
```

Keep the other fields created during runner setup. Include a port in the URL
when the HTTPS service uses a nonstandard port, for example
`https://runestra.example.com:8443`. Configure the proxy to preserve the
forwarded client protocol, and set `secure_cookies: true` for the engine's HTTPS
public URL. The engine rejects an HTTPS public URL with insecure cookies. The
runner configuration rejects plain HTTP remote URLs. The runner connects
outward, so it does not need an open inbound port.
The browser uses individual accounts and organization membership. Each runner
must be assigned an organization ID and explicit internal project IDs with
`aorch approve-runner --organization-id ... --project ...`.

In **System**, choose **Add runner**, select its project IDs and capacity, then
run the generated command on the runner host. Its setup key can be used once
and expires after one hour. The command installs `uv` if needed, enrolls the
machine, saves its token in a private file, and starts the runner. Rotating a
runner's token invalidates its previous value. `aorch revoke-runner RUNNER_ID`
blocks future requests while preserving assignments that still need
reconciliation. Runner processes execute
actions with the operating-system permissions of the user running them. Use a
dedicated OS account and restrict its filesystem and command access.

## Backups and retention

Create a new destination directory for each consistent backup:

```sh
uv run aorch backup /secure/backups/aorch-2026-10-09 --config .runtime/local/engine.yaml
```

The backup contains a SQLite snapshot, all currently retained artifacts and a
schema version manifest. Store it with access controls and encryption appropriate
for workflow data. Preserve the engine session key file with the backup, because it
protects encrypted project secrets and is required to decrypt them after restore. To restore, stop the engine, preserve the current database and
artifact directory, then replace them from a verified backup as a pair. Start the
engine and inspect the browser and logs before allowing runners to resume.

Prune artifacts and event logs for old terminal runs after confirming that their
data is no longer needed:

```sh
uv run aorch prune .runtime/local/engine.yaml --days 30
```

The default is 30 days. Pruning retains run, step and external-effect receipts,
but removes old event details and artifacts. It does not delete queued, running
or reconciling runs.

## Uncertain runs

An expired lease is not proof that the old process stopped. The engine leaves the
run in `reconciling` until the runner reports stopped processes and external
effects have been reviewed. If the runner cannot prove stop, its resource lock
remains held. Inspect the runner host and Jira/Git provider before resolving the
run. Record the operator decision in the resolution note; do not retry a comment,
push or PR action just because the connection was interrupted.

After a runner process restart, its local journal replays a completed result or
tries to stop the previous process group before reporting reconciliation. Keep
the journal and its workspace root durable across restarts. Back up the engine
database and artifacts together. Runner journals are separate local state and
need their own host backup if process recovery matters.

Execution logs are stored separately on the runner under `log_directory` (default
`logs` beside `runner.yaml`). The default retention is seven days, with a 2 GiB
runner-wide limit and a 50 MiB per-attempt limit. The browser reads logs through
the engine on demand. Offline runners and expired logs have no detailed view.
Runner logs are temporary diagnostics and are not included in engine backups.
