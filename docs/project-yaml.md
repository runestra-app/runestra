# Project YAML reference

This is the reference for `.aorch/config.yaml` and its version 3 imports. The
[configuration guide](configuration.md) explains how the files fit together.
Machine-readable schemas for editor completion and validation are in
[`schemas/`](../schemas/); `aorch validate PROJECT` also checks references and
rules that JSON Schema cannot express.

All project configuration models reject unknown fields. Duplicate YAML keys
are rejected. JSON Schema values use Draft 2020-12 and local fragment
references only.

## Root file fields, versions 1 and 2

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `version` | Integer, required | `1` or `2`. Version 1 accepts the legacy workflow `interval_seconds`. Version 2 accepts explicit workflow `triggers`. |
| `name` | Nonempty string, required | Project display name. |
| `profiles` | Mapping from profile name to profile, optional | Empty mapping. See [profiles and actions](profiles-and-actions.md#profiles-for-agent-steps). |
| `actions` | Mapping from action name to definition, optional | Empty mapping. See [profiles and actions](profiles-and-actions.md#project-defined-actions). |
| `connections` | Mapping from connection name to Jira connection, optional | Empty mapping. Each value has `url`, optional `email`, and `token_env`. See below. |
| `workflows` | Mapping from workflow name to workflow, required | At least one workflow. |

## Version 3 root file and imported files

The version 3 root file requires `version: 3` and a nonempty `name`. It may also
contain `imports` and any of the four definition mappings above. An imported
file has no version or name. It may contain `imports` and one or more definition
mappings. A definition name must be unique across the complete import graph.

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `imports` | Array of nonempty YAML file paths | No imports. Paths are relative to the importing file; absolute paths, parent traversal, backslashes and non-YAML files are rejected. |
| `profiles` | Mapping from name to profile | Each value follows the profile reference. |
| `actions` | Mapping from name to project action | Each value follows the project action reference. Built-in action names are reserved. |
| `connections` | Mapping from name to Jira connection | Each value follows the connection reference below. |
| `workflows` | Mapping from name to workflow | Each value follows the workflow and step references below. |

An imported definition's file location does not change how its prompt, skill,
or action file paths are resolved. Those paths remain relative to `.aorch`.

## Jira connection fields

| Field | Type and requirement | Constraint |
|---|---|---|
| `url` | String, required | Absolute HTTPS URL with a host. User info, query and fragment are forbidden. |
| `email` | String, optional | Jira account email. Omit when the Jira instance does not require email plus API token authentication. |
| `token_env` | Environment variable name, required | Nonempty identifier matching `[A-Za-z_][A-Za-z0-9_]*`. The actual value must be stored as a project secret with this name. |

## Workflow fields

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `discovery` | Object, optional | `source` is a nonempty source name; `target_workflow` names another, non-discovery workflow in this project. |
| `input_schema` | JSON Schema object, optional | `{"type": "object"}`. Must describe an object. Schema references and IDs must be local fragments. |
| `steps` | Mapping from step ID to step, required | At least one step. Each ID is a public Python identifier and cannot start with `_`. |
| `on_failure` | Mapping from handler ID to action step, optional | Runs after a step fails or needs attention. Handlers can reference workflow `inputs` and the `failure` object. Independent handlers continue if another handler fails. The run ends in `needs_attention` after reporting finishes. |
| `max_active_runs` | Integer, optional | `1` or greater; default `1`. |
| `interval_seconds` | Positive integer, optional | Legacy schedule supported in versions 1 and 2. Use only when `triggers` is absent. |
| `triggers` | Array with zero or one trigger, optional | Version 2 only. Cannot be combined with `interval_seconds`. See trigger fields below. |

### Discovery fields

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `source` | Nonempty string, required | Identifies the work item source, for example `jira` or `github`. |
| `target_workflow` | String, required | Must name a different workflow in the same project; the target cannot itself be a discovery workflow. |

### Trigger fields

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `type` | String, optional | `periodic` or `cron`; default `periodic`. |
| `interval_seconds` | Positive integer | Required for `periodic`; forbidden for `cron`. |
| `cron` | Five-field cron string | Required for `cron`; forbidden for `periodic`. Fields allow numbers, `*`, comma lists, ranges, and positive `/step` values. Minute `0–59`, hour `0–23`, day `1–31`, month `1–12`, weekday `0–7` where both `0` and `7` mean Sunday. |
| `timezone` | IANA time-zone name | Default `UTC`. Used by cron triggers. |
| `enabled` | Boolean | Default `true`. Set false to keep the trigger configured but inactive. |

## Step fields

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `type` | String, required | `action`, `shell`, `agent` or `approval`. Executor fields depend on this choice. |
| `needs` | Array of step IDs | Empty. Names prerequisite steps; references must exist and the resulting graph must have no cycles. |
| `when` | Condition string, optional | Evaluated before execution. May use `inputs` and available `steps` dotted references, literals, comparisons and Boolean operators. |
| `inputs` | Object | Empty object. Literal inputs for an action or step. No implicit string interpolation. |
| `input_refs` | Mapping from input name to dotted reference | Empty mapping. Normal steps can read `inputs` or prerequisite `steps`. Failure handlers can read `inputs` and `failure`. |
| `argv` | Array of strings, optional | Direct executable and arguments for an action step. First element must be nonempty. No shell parsing. |
| `action` | String, optional | Built-in or project-defined action name for an action step. Use exactly one of `action` and `argv`. |
| `command` | String, optional | Required for `shell`; cannot be combined with `argv`, `action`, `profile`, `prompt` or `prompt_text`. |
| `profile` | String, optional | Required for `agent`; must name a configured profile. |
| `prompt` | Nonempty path, optional | Path to a packaged prompt file under `.aorch`; mutually exclusive with `prompt_text`. |
| `prompt_text` | Nonempty string, optional | Inline agent instructions; mutually exclusive with `prompt`. Requires a protocol 5 runner. |
| `skills` | Array of paths | Empty. Text files or directories containing `SKILL.md`; references and symlinks must stay inside `.aorch`. Binary assets are not supported. |
| `resources` | Array of resource names | Empty. The assigned runner must advertise every resource, otherwise the step waits for a suitable runner. |
| `timeout_seconds` | Positive integer | Default `300`; limits an individual step attempt. |
| `result_schema` | JSON Schema object, optional | Validates the executor's result. Schema references and IDs must be local fragments. |
| `repeat` | Object, optional | Bounded whole-step repeat. See repeat fields below. |
| `approvers` | Array of strings, approval only | Default `[owner, admin]`; allowed values are `owner` and `admin`. |
| `approval_timeout_seconds` | Integer, approval only | Default `86400`; minimum `60`, maximum `604800`. |
| `summary_ref` | Dotted reference, approval only | Required for approval. Must begin with `inputs` or `steps`. |
| `content_ref` | Dotted reference, approval only | Required for approval. Must begin with `inputs` or `steps`. Approval is bound to the referenced content fingerprint. |

Executor-specific rules:

- `action` requires exactly one of `action` or `argv`. It cannot set
  `command`, `profile`, `prompt` or `prompt_text`.
- `shell` requires `command`. It cannot set `argv`, `action`, `profile`,
  `prompt` or `prompt_text`.
- `agent` requires a configured `profile` and exactly one of `prompt` or
  `prompt_text`. It cannot set `argv`, `action` or `command`.
- `approval` requires at least one `needs` dependency and both content
  references. It cannot set executor fields.

### Repeat fields

| Field | Type and requirement | Default and accepted values |
|---|---|---|
| `max_iterations` | Integer, required | From `1` to `100`. |
| `until` | Condition string, required | Same restricted condition syntax as `when`. May also read the current step result. |

## Project action definition fields

| Field | Type and requirement | Constraint |
|---|---|---|
| `argv` | Nonempty array of strings, required | First element is the executable. Arguments run directly, without a shell. An `@package/` argument resolves within the immutable package. |
| `files` | Array of paths | Default empty. Paths are relative to `.aorch` and included in the workflow package. |
| `input_schema` | JSON Schema object, required | Draft 2020-12. Validates resolved inputs before the script starts. |
| `result_schema` | JSON Schema object, required | Draft 2020-12. Validates the structured script result. |

The script reads JSON from stdin and writes one JSON object to stdout. Send
diagnostics to stderr. See [project-defined actions](profiles-and-actions.md#project-defined-actions)
for the complete protocol and example.

## JSON Schema limits

The committed schemas describe field shapes, types, enums, defaults and local
references. They do not express every cross-field rule in this reference, such
as dependency cycles, duplicate names across imports, cron and time-zone
compatibility, step availability, or version-specific restrictions. Those are
checked by `aorch validate` and the runtime.
