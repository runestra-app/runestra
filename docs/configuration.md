# Configure a project

The project repository stores workflow configuration in
`.aorch/config.yaml`. It declares project connections, agent profiles, reusable
actions and workflows. Keep credentials in the Runestra UI or in the private
configuration files described in the [engine and runner reference](service-yaml.md).

Use `version: 1` for a single file with legacy interval schedules. Use
`version: 2` for a single file with explicit periodic or cron triggers. Use
`version: 3` when profiles, actions, connections or workflows should be split
across YAML files. Versions 1 and 2 continue to be supported.

The [project YAML reference](project-yaml.md) describes every field, default,
allowed value and cross-field rule. Editor schemas are in
[`schemas/`](../schemas/). Configuration shape alone cannot check whether a
runner has an agent CLI installed, credentials work, or an external provider
accepts a request.

## Validate a project

Run validation from the repository root before adding or refreshing a project:

```sh
aorch validate .
aorch config show .
```

For version 3, `aorch config show --sources` also shows the imported source
files. `aorch snapshot` exports the resolved configuration and the prompt,
skill and action files included in the immutable run package. Never place
credentials in the project YAML or snapshot.

## Version 3 imports

The root `.aorch/config.yaml` holds `version: 3`, a nonempty project `name`, and
optional imports or definitions. An imported file may define one or more of
`profiles`, `actions`, `connections` and `workflows`, and may import other files.
It does not repeat `version` or `name`.

Paths in `imports` are relative to the importing YAML file and must stay within
`.aorch`. Paths used by workflow steps, including prompts and action files, are
relative to `.aorch`, including when the definition is imported. Imports may be
nested up to 16 levels and may include at most 128 files and 2 MiB of YAML in
total. Cycles, missing files, duplicate definition names, duplicate YAML keys,
and unknown fields fail validation.

## Workflow behavior

Each workflow has at least one step. `needs` connects steps into an acyclic
dependency graph; independent steps may run in parallel. A step condition can
read workflow inputs and completed dependencies. Conditions support literals,
comparisons, `and`, `or` and `not`; calls, indexing, arithmetic and private
attributes are rejected. Input references pass values without string
interpolation.

A workflow can run manually or define one periodic or cron trigger. Cron uses
five numeric fields and an IANA time zone. A skipped local time at the spring
clock change is omitted; a repeated local time in autumn runs once. If a due
run is missed or the workflow is already at capacity, the scheduler does not
start overlapping backlogged copies.

## Credentials and external changes

Project administrators set variables and secrets in the project settings.
Store provider credentials as secrets. Jira connection metadata is part of the
project YAML, but its `token_env` value names a secret stored in the project
settings. GitHub connection tokens belong on the runner. See the
[profiles and actions guide](profiles-and-actions.md) and
[operations guide](operations.md) for credential setup and the scope of each
built-in action.

Use an explicit approval step before an action that pushes Git changes, opens a
pull request, comments on or transitions a Jira issue, or otherwise publishes
work. An approval applies to the content fingerprint it reviewed. Keep
publication as a separate step so the run history shows the human decision and
the external action independently.

Inline `prompt_text` requires a protocol 5 runner. Newer built-in action
contracts also require a runner that advertises that contract version. If no
approved online runner supports a workflow's requirements, its run stays queued
until a compatible runner becomes available.
