# Security

## Runner isolation

Runestra's hosted engine does not provide shared execution runners. Each
organization connects its own runner. A runner executes workflow commands and
agent tools with the operating-system account that runs the runner process.
Treat workflow definitions and repository content as executable, untrusted
input.

Run runners under a dedicated account or disposable VM, and do not let
untrusted projects share a runner with projects that can access valuable
credentials or files. The runner account can read its runner token and any
credentials explicitly made available to its workflows. Revoke and replace
those credentials if a runner or one of its workflows is compromised.

Git fetches and project refreshes run on an organization-owned runner. The
engine allows HTTPS and SSH only on explicitly allowed hosts and their standard
ports. The default allowlist is `github.com`, `gitlab.com`, and `bitbucket.org`.
Operators who add a self-hosted Git host to `git_allowed_hosts` are responsible
for its network and DNS security. SSH host keys must match the runner's trusted
`known_hosts` file or the project's `RUNESTRA_GIT_KNOWN_HOSTS` secret.

Project secrets are encrypted in the engine database and sent to authorized
project runners over the configured engine connection. They are available to
every workflow in the project. Workflow authors therefore have access to all
project secrets. Secret masking catches literal values in logs and results, but
cannot prevent workflow code from transforming a value before outputting it.
Treat workflows and their repository content as trusted with every project
secret, or use separate projects and runner permissions for different trust
levels.

## Reporting a vulnerability

Do not publish access tokens, credentials, or exploit details in a public
issue. Use GitHub's private vulnerability reporting for this repository when
available. Include the affected version, impact, and a minimal reproduction.
