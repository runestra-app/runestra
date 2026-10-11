# Deployment

For the quickest source demo, use the [README quick start](../README.md#quick-start).
It needs no Docker or email setup. This guide covers release installation and a
persistent server deployment.

## Install a published release locally

The installer requires a published GitHub release. If none is available, it
stops before installing dependencies; use the source quick start instead.
It installs the engine in Docker and runs the runner on the host so it can use
local agent CLI login and tools.

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/runestra-app/runestra/main/scripts/install-local.sh)"
```

Choose `local` explicitly for loopback-only testing or `email` for ordinary
sign-in. Email mode needs Brevo credentials and the initial owner's details.
The engine is available at `http://127.0.0.1:8765`; keep the runner terminal open.
Docker may require OS permissions or Desktop first-run setup.

To select a release tag, prefix the installer command with
`RUNESTRA_VERSION=v0.1.0`, replacing that example with an actual published tag.
Rerunning the installer updates to the latest release and retains `.runtime`.
It refuses to replace a checkout containing local changes. Stop active work and
the previous runner before an update.

Restart the host runner from a managed installation:

```sh
cd ~/.local/share/runestra
uv run --locked aorch runner .runtime/local/runner.yaml
```

## Deploy the engine on a Linux VM

This guide runs the engine in Docker Compose on one Linux VM. It keeps the
engine behind an HTTPS reverse proxy and lets runners connect outward. The
Compose port is bound to loopback so the engine is not exposed directly.

Install Docker Engine and the Docker Compose v2 plugin using the instructions
for your Linux distribution. Install `uv`, then clone Runestra and create its
persistent local configuration:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="${HOME}/.local/bin:${PATH}"
git clone https://github.com/runestra-app/runestra.git
cd runestra
uv sync --locked
uv run aorch init-local examples/local-actions --directory .runtime/local --project-mount-path /workspace/examples/local-actions
```

Set the numeric UID and GID for the account that owns `.runtime/local`, then
start the engine:

```sh
export RUNESTRA_UID="$(id -u)"
export RUNESTRA_GID="$(id -g)"
docker compose up -d --build
```

Edit `.runtime/local/engine.yaml` and set `public_base_url` to the HTTPS URL
that users will open. Set `secure_cookies: true`. Configure a real Brevo API
key in `.runtime/local/auth-secrets.yaml`, then restart the engine. The
`no-reply@runestra.app` sender must be verified in Brevo.

Configure the host reverse proxy to forward HTTPS requests to
`http://127.0.0.1:8765`, preserving the original host and HTTPS scheme. Allow
public inbound traffic only on the proxy's HTTPS port. Keep port 8765 private.
Create the initial account before opening registration to users:

```sh
uv run aorch bootstrap-owner --email you@example.com --username you --organization your-org --config .runtime/local/engine.yaml
```

Open the configured HTTPS URL and request the email sign-in link. In **System**,
choose **Add runner** and run the generated command on each runner host. The
runner initiates its connection to the engine; it needs outbound HTTPS access,
not an inbound firewall rule.

To update from the cloned checkout, stop or drain active runs, then run:

```sh
git pull --ff-only
uv sync --locked
docker compose up -d --build
```

The database and artifacts live in `.runtime/local`. Back them up together
using `aorch backup`; store the backup outside the VM. For runner recovery,
preserve its journal and workspace too. Read [operations](operations.md) before
using public projects or granting agents write access.

The convenience installer is intended for local machines. This guide is the
provider-neutral VM path. A cloud-specific deployment still needs its own
network, firewall, DNS, TLS, backup, and update configuration.

## Remove a managed release installation

From `~/.local/share/runestra`, stop the runner with Ctrl+C, then run
`bash scripts/uninstall-local.sh`. The engine is stopped and `.runtime` is
moved to `${XDG_STATE_HOME:-$HOME/.local/state}/runestra/runtime`; the next
installer run restores it. To remove the data too, pass `--purge-data` and type
`DELETE` when prompted. Set `RUNESTRA_INSTALL_DIR` and `RUNESTRA_STATE_DIR` to
override the installation and preserved-data paths.
