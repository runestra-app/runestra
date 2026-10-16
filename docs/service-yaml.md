# Engine and runner YAML reference

The engine reads `engine.yaml`; each runner reads its own `runner.yaml`. Paths
are resolved relative to the YAML file. Engine provider credentials and webhook
signing secrets belong in the separate `secrets_file`, which must have file
permissions `0600`. Unknown top-level fields are rejected.

Examples are [`engine-auth.example.yaml`](../examples/engine-auth.example.yaml)
and [`auth-secrets.example.yaml`](../examples/auth-secrets.example.yaml).
After creating the secret file, restrict it with `chmod 600 auth-secrets.yaml`.
Editor schemas are in [`schemas/`](../schemas/). Custom security rules and
relationships between settings are also checked when the service starts.

## Engine YAML

| Field | Type | Default or constraint |
|---|---|---|
| `database` | Path | `engine.sqlite`. Stores engine state and run history. |
| `artifacts` | Path | `artifacts`. Stores run artifacts. |
| `host` | Host name or IP | `127.0.0.1`. A non-loopback bind requires secure cookies and HTTPS, or an explicitly loopback-published proxy/Compose port. |
| `port` | Integer | `8765`; range `1–65535`. |
| `admin_token_file` | Optional path | No default. Private file used for engine administration. |
| `session_key_file` | Path, required | No default. Protect and back up this file with the database; losing it makes saved project secrets unreadable. |
| `secure_cookies` | Boolean | `false`. Required for HTTPS public URLs and remote engine binds. |
| `public_base_url` | Absolute HTTP(S) URL | `http://127.0.0.1:8765`. Remote hosts require HTTPS; HTTPS requires `secure_cookies`. |
| `git_allowed_hosts` | Array of host names | `github.com`, `gitlab.com`, `bitbucket.org`. Must be nonempty, lowercase DNS names; duplicates are removed. |
| `registration_enabled` | Boolean | `true`. Controls open account registration. |
| `local_auth` | Boolean | `false`. Available only for loopback HTTP and a loopback bind or explicitly loopback-published port. |
| `loopback_port_forward` | Boolean | `false`. Use only when the process is behind a proxy or Compose port explicitly bound to host loopback. |
| `session_ttl_seconds` | Integer | `28800`; range `300–2592000`. |
| `magic_link_ttl_seconds` | Integer | `900`; range `60–3600`. |
| `invitation_ttl_seconds` | Integer | `604800`; range `3600–2592000`. |
| `secrets_file` | Optional path | No default. Path is resolved relative to `engine.yaml`; file mode must be `0600`. |
| `email_from` | String | `no-reply@runestra.app`. Sender address used for login email. |
| `google_client_id` | String | Dummy placeholder. Set the OAuth client ID when Google login is configured. |
| `github_client_id` | String | Dummy placeholder. Set the OAuth client ID when GitHub login is configured. |

`brevo_api_key`, `google_client_secret`, `github_client_secret` and
`webhook_triggers` are **not accepted in `engine.yaml`**. Put them in the YAML
file named by `secrets_file`:

```yaml
brevo_api_key: YOUR_BREVO_KEY
google_client_secret: YOUR_GOOGLE_CLIENT_SECRET
github_client_secret: YOUR_GITHUB_CLIENT_SECRET
webhook_triggers: {}
```

The allowed secret file fields are those four keys. Do not commit real values.
Webhook definitions have a unique ID of 1–64 letters, digits, underscore or
hyphen. Each defines `provider` (`github` or `hmac`), a signing `secret` of at
least 32 characters, a target `project` and `workflow`, and a nonempty
`input_mapping` from workflow input identifiers to payload paths. GitHub
definitions also require an `owner/repository` allowlist; `event` defaults to
`issues` and `actions` to `[opened]`. Generic HMAC webhooks use the signed raw
request format described in the [operations guide](operations.md).

## Runner YAML

| Field | Type | Default or constraint |
|---|---|---|
| `id` | String, required | Stable runner identifier. |
| `engine_url` | Absolute URL | `http://127.0.0.1:8765`. Remote engines require HTTPS; HTTP is allowed for loopback hosts. Credentials, query strings and fragments are forbidden. |
| `token_file` | Path, required | Runner authentication token. Keep it private. |
| `workspace_root` | Path | `workspaces`. Run workspaces are created under this directory. Agent attempts get a private `.aorch-agent-scratch` directory beside the checkout; it is outside the Git working tree and removed after each attempt. |
| `journal` | Path | `runner.sqlite`. Stores local runner and recovery state. |
| `log_directory` | Path | `logs`. Resolved relative to `runner.yaml`. |
| `log_retention_days` | Integer | `7`; range `1–365`. |
| `log_max_total_bytes` | Integer | `2147483648` (2 GiB); minimum `1024`. |
| `log_max_attempt_bytes` | Integer | `52428800` (50 MiB); minimum `1024`. |
| `capacity` | Integer | `1` or greater. Maximum simultaneous step attempts. |
| `adapters` | Array of adapter names | Default `[action, shell]`. Known adapters: `action`, `shell`, `codex`, `claude`, `openai`. Only advertise adapters configured and available on this machine. |
| `environment` | Array of environment variable names | Empty. Adds named variables to the allowlist passed to workflow subprocesses. |
| `connections` | Mapping from connection name to string settings | Empty. GitHub connections use `token_env`; its named credential stays on the runner and is withheld from workflow subprocesses. |
| `repositories` | Mapping from repository name to local path | Empty. Used by `git.prepare`; paths resolve relative to `runner.yaml`. |
| `allow_file_git_sources` | Boolean | `false`. Allows local file Git URLs when explicitly enabled. |
| `devices` | Mapping from device name to settings | Empty. `device.test` supports `platform: ios` with simulator `id`, or `platform: android` with device `id` and optional `emulator` and `avd`. |
| `api_credentials` | Mapping from provider to environment variable name | Empty. Provider keys used by agents are `codex`, `claude` and `openai`. The variable name is allowlisted; its value is read on the runner. |
| `redact_env` | Array of environment variable names | Empty. These variables are withheld from subprocess environments and redacted from logs. |
| `command_allowlist` | Array of executable names or paths | Empty. Required for command tool access by the OpenAI API agent. |
| `resources` | Array of resource names | Empty. Advertises exclusive locks that workflow steps may request. |

Runner `connections`, `devices` and `repositories` are keyed by names chosen by
the operator. Each API credential value must be an environment variable name.
Environment variables allowlisted for workflow commands are still filtered if
they contain a configured credential, project secret, or redacted value.
