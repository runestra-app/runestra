# Architecture

Runestra separates coordination from execution. The engine owns durable state,
scheduling and browser access. Runners make outgoing connections to the engine
and execute trusted project code with their host OS permissions.

## Components and ownership

| Component | Responsibility | Source |
|---|---|---|
| CLI | Validation, snapshots, local setup, enrollment and operations | `src/aorch/cli.py` |
| Configuration | Strict YAML, imports, source diagnostics and immutable packages | `src/aorch/config/` |
| Contracts | Engine/runner messages and transport version | `src/aorch/contracts/` |
| Engine | Authenticated API, SQLite state, scheduler and notifications | `src/aorch/engine/` |
| Browser | Projects, workflows, runs, approvals, answers and administration | `src/aorch/engine/web/` |
| Runner | Registration, heartbeat, leases, process control, journal and workspaces | `src/aorch/runner/` |
| Built-in actions | Versioned operations, schemas and external-effect receipts | `src/aorch/extensions/` |

The engine uses FastAPI and Uvicorn. SQLite stores workflow revisions, the
queue, run state, authorization and effect records. Run one engine and scheduler
per database. Shared-network SQLite, multiple active engines and PostgreSQL are
not supported by the current implementation.

## Three configuration boundaries

1. A project's `.aorch/config.yaml` defines workflows, profiles, actions and
   connection metadata. Versions 1 and 2 use one file; version 3 supports local
   imports for profiles, actions and workflows. Duplicate names are errors,
   rather than overrides determined by import order.
2. `engine.yaml` defines the engine's bind address, public URL, database,
   artifacts and authentication. Project registrations live in the database.
3. `runner.yaml` defines the runner identity, engine URL, private token,
   execution capabilities, workspace, journal and resources.

Prompt, skill and packaged action files are captured in a validated immutable
snapshot. Runs pin that snapshot and, for Git projects, the source commit.
Later project edits affect later runs. A Git runner fetches the pinned commit,
including when the remote branch has advanced. See the user-facing
[configuration guide](../docs/configuration.md) and
[project YAML reference](../docs/project-yaml.md) for supported syntax and
import restrictions.

Configuration version, transport protocol and action contract version are
separate concepts. The current transport is protocol **5**, defined in
`src/aorch/contracts/__init__.py`; inline `prompt_text` requires protocol 5.
Runners advertise supported action contracts. An incompatible runner is not
eligible for a workflow requiring unsupported contracts.

## Execution and persistent state

Workflow steps form a DAG. `needs` determines ordering, `when` determines
whether a step runs, and `input_refs` passes explicit values from completed
dependencies. There is no implicit text interpolation. A step executes an
agent, shell command, named action or argument-array command. Approval steps
are managed by the engine.

The engine claims work atomically under organization, project and capacity
constraints. Runner heartbeat and lease renewal keep the assignment current.
Results are validated against their contracts and accepted idempotently.
Stale assignment generations cannot complete newer attempts.

Each runner keeps a durable journal. After restart it can replay a completed
result or reconcile previous processes. An expired lease does not prove that
execution stopped: uncertain attempts enter `reconciling` and require process
stop confirmation or an operator decision. See
[operations](../docs/operations.md).

`needs_input` records a completed attempt requiring clarification. Browser
answers create a linked new run with a fresh snapshot and validated inputs.
Approval waits preserve the run workspace and release runner capacity. The
publication path checks the verified content identity; changing content after
verification requires new verification and approval.

A coding run stays with its assigned runner and workspace. Moving an active
workspace between runners is not implemented.

## Actions and external effects

`ACTION_REGISTRY` is the common source for built-in names, schemas, versions,
capability requirements and dispatch. Project actions package scripts and
explicit input/result schemas. Supported contracts are documented in the
[profile guide](../docs/profiles-and-actions.md) and
[built-in action reference](../docs/actions.md), and exposed by `aorch actions`.

Commit, push, comments and PR creation are explicit workflow actions. Git
verification records content identity; publication checks the relevant receipt.
External-effect records and provider markers support reconciliation after a
lost response. A local receipt alone does not prove remote success, and an
uncertain write is not blindly repeated. Current PR publication uses GitHub;
Azure DevOps and Bitbucket publication remain proposed in the
[provider specification](design/pull-request-providers.md).

## Identity and trust

Browser users belong to organizations with owner, admin and member roles.
Projects and runners are assigned to organizations. Membership and project
access are checked on the server for reads and writes; choosing an organization
in the browser does not grant access. Runner tokens authorize runner operations,
not browser administration.

Project variables and secrets travel only with authorized assignments. Secret
values are encrypted at rest; the encryption key depends on the engine session
key, which must be backed up with the database. Built-in provider actions use
scoped credentials. Sensitive provider credentials are excluded from ordinary
shell and agent subprocess environments.

Process isolation is the OS user's responsibility. Worktrees, prompts and JSON
schemas do not sandbox hostile code. Agent adapters differ in tool and sandbox
support, and cloud model requests may send project content to a provider. Read
[security](../SECURITY.md) and the adapter notes in
[profiles and actions](../docs/profiles-and-actions.md).

## Logs, artifacts and retention

The engine retains status, summaries, structured results, events and artifacts.
Detailed execution logs are stored separately on the runner and read through
the engine on demand. Activity and raw views share the captured log record;
raw still means redacted diagnostic data. Offline or expired logs cannot be
retrieved. Runner logs are not included in engine backups.

Retention and recovery are operational concerns documented in
[operations](../docs/operations.md#backups-and-retention). Historical design decisions
and earlier protocol versions are retained in the
[planning archive](archive/planning-history.md); they do not override current
contracts or user documentation.
