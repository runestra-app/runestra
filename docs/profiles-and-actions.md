# Agent profiles and project actions

This page documents the `profiles` and `actions` sections in a project's
`.aorch/config.yaml`. Profiles configure AI agent steps. Actions run a named
operation such as a project script or a built-in Git or Jira operation.

## Profiles for agent steps

A profile is a reusable execution policy for one or more steps with
`type: agent`. Define profiles at the top level, then refer to one by name from
the step. A profile does not define the task prompt. Set either `prompt` to a
prompt file under `.aorch` or `prompt_text` to write the instructions directly
in the workflow configuration.

```yaml
profiles:
  reviewer:
    adapter: claude
    authentication: local
    sandbox: read-only
    tools: [read]

workflows:
  review:
    steps:
      inspect:
        type: agent
        profile: reviewer
        prompt: prompts/review.md
```

Use `prompt_text` for inline instructions. A YAML block scalar preserves
multiple lines:

```yaml
workflows:
  review:
    steps:
      inspect:
        type: agent
        profile: reviewer
        prompt_text: |
          Review the current changes.
          Report bugs and missing acceptance criteria.
```

Each agent step must define exactly one of `prompt` or `prompt_text`.
The engine requires a protocol 5 runner for workflows that use `prompt_text`.
Upgrade the engine and runner together before enabling those workflows.

Supported profile fields:

| Field | Values and behavior |
| --- | --- |
| `adapter` | Required. `claude`, `codex`, or `openai`. Selects the agent runtime installed or configured for the runner. |
| `authentication` | Required. `local` uses the runner's locally authenticated CLI. `api` uses an API credential configured on the runner. The OpenAI adapter requires `api`. |
| `model` | Model name passed to the selected runtime. Optional for Claude and Codex, which can choose their defaults. Required for OpenAI API profiles. |
| `credential_env` | Optional environment variable name for the API credential. It must match the provider's entry in the runner's `api_credentials` configuration. Put the variable's secret value on the runner, never in the project file. |
| `tools` | Any combination of `read`, `write`, and `command`. Defaults to all three. These enable workspace file reading, workspace file writing, and command execution respectively. |
| `sandbox` | `read-only` or `workspace-write`. Defaults to `workspace-write`. `read-only` cannot be combined with the `write` tool. Codex receives this as its sandbox mode. Claude does not receive an OS filesystem sandbox; its configured tool list and permission mode control tool use, and allowing `Bash` can run commands with the runner account's filesystem rights. |
| `additional_writable_roots` | Codex only. Optional list of existing, absolute, narrow directories to add to Codex's writable roots. Defaults to empty. Only available with `sandbox: workspace-write`. |
| `max_iterations` | OpenAI API agent limit, from 1 to 100, default 12. |
| `max_output_tokens` | Maximum generated tokens per OpenAI API response, minimum 128, default 4096. |
| `max_total_tokens` | Maximum combined OpenAI API token usage, minimum 128, default 50000. |
| `max_budget_usd` | Positive cost limit, default 1 USD. Passed to Claude CLI and enforced by the OpenAI API agent. |
| `input_price_per_million` | Optional input token price in USD per million tokens. Required with the output price for OpenAI API profiles so the runner can estimate and enforce cost. |
| `output_price_per_million` | Optional output token price in USD per million tokens. Required with the input price for OpenAI API profiles. |

The token limits and configured token prices are used by the OpenAI API agent.
The current Claude adapter passes `max_budget_usd` and `model` to Claude CLI;
the current Codex adapter passes `sandbox` and `model`. Do not assume limits
that are not passed to a runtime are enforced by that runtime.

For Codex and Claude CLI agent steps, the runner creates a private scratch
directory for each attempt beside the repository checkout, outside the Git
working tree. It sets `RUNESTRA_ATTEMPT_SCRATCH`, `TMPDIR`, `TMP`, `TEMP`,
`XDG_CACHE_HOME`, and common Python, Rust, Go, Node, Gradle, and compiler cache
and build variables to subdirectories there. The scratch directory is removed
when the attempt finishes. `HOME` is left unchanged, and workspace-write
remains the default Codex sandbox. Tools that ignore these variables can still
be denied when writing outside the workspace and configured roots.

The runner operator can set `agent_execution: trusted-host` in `runner.yaml`:

```yaml
id: macos-build
token_file: runner.token
workspace_root: workspaces
agent_execution: trusted-host
```

This setting is not available in project workflow configuration and defaults to
`sandboxed`. In `trusted-host` mode the Codex and Claude CLIs run without their
filesystem sandbox or permission prompts. They can read and change any files
available to the runner's operating-system account. Workflow scripts, package
manifests, build phases, and tests execute with runner permissions too, so an
agent sandbox alone is not a security boundary for the full workflow. Use this
mode only on a dedicated OS account or VM with limited files and credentials.
The attempt scratch environment remains configured in this mode for cache and
build hygiene. There is no automatic fallback from sandboxed to trusted-host.

Codex users can grant one extra, existing directory when a tool cannot use the
attempt scratch directory. This expands the locations the agent can modify, so
keep each root specific to the tool or project. Do not set a home directory,
system directory, temporary root, or repository parent as an additional root.
The runner rejects relative paths, filesystem roots, common broad system roots,
roots that contain the runner workspace, and a whole home directory. A
specific subdirectory inside a home directory can be used when appropriate.

```yaml
profiles:
  builder:
    adapter: codex
    authentication: local
    sandbox: workspace-write
    additional_writable_roots:
      - /srv/runestra/tool-cache/my-project
```

Create the directory on the runner before executing the workflow. A sandbox
denial is recorded in the step log as `sandbox.denied`; the structured failure
includes the denied path and tool when Codex provides the path. If a tool cannot
redirect its write, use that diagnostic to decide whether a narrowly scoped
extra root is justified. Do not retry by broadening filesystem access.

For a read-only code review, use a profile such as:

```yaml
profiles:
  reviewer:
    adapter: codex
    authentication: local
    sandbox: read-only
    tools: [read]
```

For an agent that creates a file and needs the system clock, grant only the
capabilities it needs:

```yaml
profiles:
  writer:
    adapter: claude
    authentication: local
    sandbox: workspace-write
    tools: [read, write, command]
```

The runner must have the selected CLI installed and authenticated for local
authentication. For API authentication, configure the credential on the runner.
Project validation checks the configuration shape, it does not test installed
CLIs, credentials, model access, or account billing.

## Project-defined actions

Use a project-defined action when a workflow needs a repeatable script with a
validated input and output contract. Declare it in the top-level `actions`
mapping, then call it from a step whose `type` is `action`.

```yaml
actions:
  publish_note:
    argv: [python3, '@package/scripts/publish_note.py']
    files: [scripts/publish_note.py]
    input_schema:
      type: object
      properties:
        filename:
          type: string
          pattern: '^[0-9]{8}-[0-9]{6}-test\.md$'
        message:
          type: string
          minLength: 1
      required: [filename, message]
      additionalProperties: false
    result_schema:
      type: object
      properties:
        status:
          type: string
          enum: [succeeded, needs_input, failed, cancelled, needs_attention]
        summary: {type: string}
        outputs: {type: object}
        workspace_revision: {type: [string, 'null']}
        error: {type: [string, 'null']}
      required: [status, summary, outputs]
      additionalProperties: false

workflows:
  analyze:
    steps:
      publish:
        type: action
        action: publish_note
        inputs:
          filename: report.md
          message: Publish generated report
```

Action definition fields:

| Field | Meaning |
| --- | --- |
| `argv` | Required nonempty executable and argument array. Arguments are passed directly to the process, without shell interpolation. An argument beginning with `@package/` resolves to a file inside the packaged `.aorch` snapshot. |
| `files` | Optional paths relative to `.aorch`. These files are included in the immutable workflow snapshot. Include the script and any other files it imports or reads. |
| `input_schema` | Required JSON Schema Draft 2020-12 object. The runner validates the final action inputs before starting the script. |
| `result_schema` | Required JSON Schema Draft 2020-12 object. The runner validates the script's structured result against it. |

The action step's `inputs` supply literal values. `input_refs` can map values
from workflow inputs or completed prerequisite outputs, for example
`filename: steps.create_note.outputs.filename`. The script receives JSON on
stdin. Its top-level object contains `inputs`, `step_inputs`, and `steps`;
`step_inputs` is the action's final input object after applying `input_refs`.
The script must print one JSON object to stdout. Its result must include
`status` and `summary`; normally it also includes `outputs`. The result must
match `result_schema`. Send diagnostics to stderr so stdout remains valid JSON.

Keep schemas narrow. Use `required` for mandatory fields and
`additionalProperties: false` to reject accidental or misspelled inputs. The
JSON Schema `properties`, `required`, `enum`, `pattern`, `minLength`, and
`maxLength` keywords are useful for explicit contracts. Validate a project with
`uv run aorch validate path/to/project` after changing its action definitions.

### Built-in actions

Built-in actions are selected with the same `type: action` and `action:` fields,
but do not need a project `actions` declaration. Their accepted inputs and
results are documented in the complete
[built-in action reference](actions.md). The machine-readable action contracts
are in [`schemas/actions.json`](../schemas/actions.json).

The summary table below lists the required and optional input names:

Jira actions resolve `connection` against the project's `connections` mapping.
Each Jira connection declares its HTTPS `url`, optional `email`, and the
`token_env` name of a project secret.

| Action | Required inputs | Optional inputs |
| --- | --- | --- |
| `github.discover` | `connection`, `repository` | `assignee`, `labels`, `state` |
| `github.get` | `connection`, `repository`, `issue` | None. |
| `jira.discover` | `connection` | `jql`, `assignee`, `status`, `project`. Provide `jql`, or both `assignee` and `status`. |
| `jira.comment` | `connection`, `issue`, `ambiguities` | None. Each ambiguity has required string fields `id` and `question`. |
| `jira.report_failure` | `connection`, `issue`, `run_id`, `failed_step`, `status`, `summary`, `error`, `attempt_id` | Optional diagnostic object and artifact list. Writes one idempotent failure report comment. |
| `jira.transition` | `connection`, `issue`, `status` | None. Transitions to the named status when Jira exposes an available transition. |
| `git.prepare` | `repository` | `item_id`, `base` |
| `git.branch` | None |
| `git.verify` | `commands` | `paths`, `only_paths`. Each command is an argument array. |
| `git.commit` | `message` | `paths` |
| `git.push` | None | `remote` |
| `git.pr` | `repository`, `base`, `title`, `body` | `remote` |
| `github.open_pr` | `repository`, `base`, `title`, `body` | `remote` |
| `coding.repair` | `implement_profile`, `implement_prompt`, `review_profile`, `review_prompt`, `test_commands` | `max_iterations`, `skills` |

| `device.test` | `device`, `commands` | None. Each command is an argument array. |
| `core.needs_input` | None | `summary`, `ambiguities` |

For `coding.repair`, one iteration means one implementation call, one review call, and the configured checks if the review approves. The default is three iterations, so a run can make up to six agent calls. Set `max_iterations: 1` for a small, low-risk task when you want to limit agent calls; review rejection or a failed check then stops the step without another repair attempt.

Each built-in action has a versioned contract. The runner validates versioned
action outputs before returning them, and the engine validates them again when
it accepts a result. New runners advertise supported contract versions during
registration. The engine leaves runs queued when no approved online runner
supports every required contract.

List built-in actions and inspect their current input and result contracts from
the CLI:

```sh
aorch actions list
aorch actions list --json
aorch actions describe github.open_pr --json
```

Built-in actions use the runner's `action` adapter capability. The CLI catalog
does not confirm that the runner has the required local Git, GitHub CLI, device,
or provider credentials installed.

Example of built-in actions:

```yaml
steps:
  prepare:
    type: action
    action: git.prepare
    inputs:
      repository: git@github.com:example/project.git
      base: main
  verify:
    type: action
    action: git.verify
    needs: [prepare]
    inputs:
      commands:
        - [python3, '-m', 'pytest', '-q']
```

An action step can also run a one-off command directly with `argv`, without a
named action or action schema:

```yaml
steps:
  show_status:
    type: action
    argv: [git, status, --short]
```

`git.branch` prepares an isolated branch in the checked out project repository.
`git.prepare` prepares an isolated worktree from a runner-configured local
repository. Both record the workspace mode, owner and starting commit. The
executor keeps that mode for later workflow steps, so a worktree is not reset
by the project's pinned-checkout preparation.
`git.verify.paths` checks that each declared file exists. Set `only_paths: true`
to reject any other tracked or untracked changes. `git.commit` can restrict the
commit to the same relative `paths`; unrelated staged changes make the action
fail. Versioned `git.push` results include the remote, branch and commit.
`github.open_pr` uses the same idempotent GitHub PR flow as the legacy `git.pr`
name and returns its URL, number, branch, base and verified head commit.

Choose exactly one of `action` or `argv` for an action step. Use `shell` for a
shell command string. Do not put credentials in action inputs, config files, or
prompts. Configure provider credentials on the runner. Built-in and project
actions use the runner's `action` adapter capability.
