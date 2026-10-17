# Built-in action reference

Built-in actions run on a runner with the `action` adapter. Write them as steps
with `type: action`, `action: ACTION_NAME` and optional `inputs` or
`input_refs`. Input references supply resolved values from workflow inputs and
completed prerequisites. Values are validated after references are resolved.

The machine-readable source for required fields, types, enums, limits, aliases,
outputs and action contract versions is [`schemas/actions.json`](../schemas/actions.json).
For an installed CLI, inspect the same live contract with
`aorch actions describe ACTION_NAME --json`. The tables below explain the
accepted input fields and their purpose.

All string fields marked **text** must be nonempty. Unless listed as an enum,
they accept any nonempty string. Unknown input fields are rejected.

## GitHub and Jira actions

| Action | Input fields |
|---|---|
| `github.discover` | Required: `connection` (runner connection name), `repository` (`OWNER/REPOSITORY`). Optional: `state` (`open`, `closed` or `all`, default `open`), `labels` (array of nonempty strings), `assignee` (text). |
| `github.get` | Required: `connection`, `repository` (`OWNER/REPOSITORY`), `issue` (integer at least `1`). |
| `jira.discover` | Required: `connection` (project Jira connection name). Optional: `jql` (text), `assignee` (text), `status` (text), `project` (text). Provide `jql`, or both `assignee` and `status`; `project` further filters the generated query. |
| `jira.comment` | Required: `connection`, `issue` (Jira key), `ambiguities` (array with at least one item). Each item requires nonempty `id` and `question` strings. Adds a reconciled comment asking the listed questions. |
| `jira.report_failure` | Required: `connection`, `issue`, `run_id`, `failed_step`, `status`, `summary`, `error`, and `attempt_id`. Optional: structured `diagnostics` and uploaded `artifacts`. Adds one idempotent technical failure report comment. |
| `jira.transition` | Required: `connection`, `issue`, `status` (target status name). Jira must expose an available transition to that status. |

GitHub connections are configured on the runner as a named mapping with a
`token_env` entry. The named credential remains on the runner. Jira connections
are declared in project YAML with `url`, optional `email` and `token_env`; the
token value is stored as a project secret. See the
[service YAML reference](service-yaml.md#runner-yaml) and
[project YAML reference](project-yaml.md#jira-connection-fields).

## Git and pull request actions

| Action | Input fields |
|---|---|
| `git.prepare` | Required: `repository` (name configured under runner `repositories`). Optional: `item_id` (text used to derive the run branch), `base` (text Git revision; default `HEAD`). |
| `git.branch` | No inputs. Creates or resumes a branch in the pinned project checkout. |
| `git.verify` | Required: `commands` (nonempty array of nonempty string argument arrays; commands execute directly, without a shell). Optional: `paths` (nonempty array of nonempty paths), `only_paths` (boolean). When set, `only_paths: true` rejects changes outside the declared paths. |
| `git.commit` | Required: `message` (text). Optional: `paths` (nonempty array of paths), `approval_fingerprint` (64 lowercase hexadecimal characters). With `paths`, only those paths can be committed; unrelated staged changes cause failure. |
| `git.push` | Optional: `remote` (text Git remote name). Pushes and verifies the run commit. |
| `git.pr` | Required: `repository`, `base`, `title`, `body` (text). Optional: `remote` (text remote). Creates or reconciles a GitHub pull request. |
| `github.open_pr` | Same inputs, behavior and contract as `git.pr`; this is the current descriptive name. |

`git.prepare` requires its repository name to exist in runner configuration.
Before using `git.commit`, `git.push` or a pull request action, prepare a Git
workspace with the required earlier steps. Use an `approval` step before
publishing changes.

## Agent repair, device and clarification actions

| Action | Input fields |
|---|---|
| `coding.repair` | Required: `implement_profile`, `implement_prompt`, `review_profile`, `review_prompt` (text names or paths), `test_commands` (nonempty array of argument arrays). Optional: `max_iterations` (integer `1–20`, default `3`), `skills` (array of text file or skill directory paths). One iteration runs implementation, review and, when approved, the configured checks. |
| `device.test` | Required: `device` (name from runner `devices`), `commands` (nonempty array of argument arrays). The workflow step must reserve the same device name in `resources`. |
| `core.needs_input` | Optional: `summary` (string, including empty), `ambiguities` (array of objects). Stops the workflow and requests human input. |

## Results

Action results are available through `steps.STEP_ID.outputs.FIELD` references.
The currently declared result fields are:

| Action | Result fields |
|---|---|
| `github.discover` | `work_items`, an array of objects. Each item requires `id`; optional fields are `title`, `description`, `url`, `labels`, `status` and `updated`. |
| `github.get` | `work_item`, with the same item fields as above. |
| `git.prepare` | `repository`, `branch`, `starting_commit`, `mode: worktree`, `owner`. |
| `git.branch` | `repository`, `branch`, `starting_commit`, `mode: checkout`, `owner`. |
| `git.verify`, `coding.repair` | `verified: true`, `fingerprint` (64 lowercase hexadecimal characters). |
| `git.commit` | `commit`, `branch`. |
| `git.push` | `remote`, `branch`, `commit`. |
| `git.pr`, `github.open_pr` | `pull_request`, `number` (positive integer), `branch`, `commit`, `head_commit`, `base`, `external_effects`. |
| `jira.discover` | `work_items`, an array of objects containing `id`, `summary`, `description`, `comments`, `attachments` and `provider`. The provider object preserves Jira's returned issue fields. |
| `jira.comment` | `external_effects`, an array containing the reconciled effect key, `kind: jira.comment` and Jira `provider_id`. |
| `jira.transition` | `issue` and `status`. |
| `device.test` | No JSON output fields; returns a summary and report or screenshot artifacts. |
| `core.needs_input` | Returns the supplied `summary` and `ambiguities` in outputs and stops the run with `needs_input`. |

Successful results are checked against a closed output schema only for action
contracts that declare one. Catalog versions and compatibility requirements
are listed in `schemas/actions.json`; the engine schedules the step only on a
runner that advertises the required contract version.
