# Write a workflow

Create `.aorch/config.yaml` in the root of the project repository. The file
names the project, defines any AI profiles, and lists workflows. Each workflow
has one or more uniquely named steps. A step can be an `agent`, `shell`, or
`action`. Steps run when their `needs` have succeeded. Independent steps can run
in parallel.

Here is a complete example with an AI review followed by a dependent shell
check and a per-workflow periodic trigger:

```yaml
version: 2
name: sample-project
profiles:
  reviewer:
    adapter: claude
    authentication: local
    sandbox: read-only
    tools: [read]
workflows:
  review:
    max_active_runs: 1
    triggers:
      - type: periodic
        interval_seconds: 3600
        enabled: true
    steps:
      inspect:
        type: agent
        profile: reviewer
        prompt: prompts/review.md
        timeout_seconds: 900
      summarize:
        type: shell
        needs: [inspect]
        command: "printf 'Review step completed\\n'"
```

Put the prompt at `.aorch/prompts/review.md`. Prompt and skill paths are
relative to `.aorch`, and referenced text files are captured with the workflow
revision. The runner executes in the checked out project workspace, so an agent
can inspect project files. Choose `sandbox: read-only` and `tools: [read]` for
analysis that must not edit files. Add `write` when the agent needs to create or
change project files. Add `command` only when it needs terminal tools such as
`date`, tests, or Git. The runner must have the selected adapter installed.

`needs` defines the dependency graph. Use it whenever a step consumes a prior
step's result or must wait for that step. `when` is an optional condition, for
example `steps.inspect.outputs.decision == 'ready'`. Conditions and
`input_refs` can refer only to the current step's inputs and completed
dependencies. `input_refs` passes a value from a previous result into the next
step without string interpolation. In this fragment, replace `some.action`
with a built-in action or one declared in the top-level `actions` section:

```yaml
      publish:
        type: action
        action: some.action
        needs: [inspect]
        input_refs:
          report: steps.inspect.outputs.report
```

Version 2 expresses a periodic trigger inside each workflow. Omit `triggers`
for a manually started workflow. The older version 1 `interval_seconds` field
is still accepted. Periodic Git workflows refresh the configured ref before
starting a run, then pin that commit for the run.

The top-level `actions` section can define project actions with an `argv`,
packaged files, and input and result JSON schemas. Built-in actions include Git,
Jira, device testing, and coding repair. Use schemas to validate the data passed
between a project step and its action. Never put credentials in this file or in
a prompt.

Use `on_failure` when a workflow must report technical failures before it
stops. Handlers run after a failed or attention-required step and receive
`failure.step`, `failure.status`, `failure.summary`, `failure.error`,
`failure.run_id`, `failure.attempt_id`, `failure.diagnostics`, and
`failure.artifacts`. `failure.failures` lists every failed step when multiple
steps fail at once. Independent handlers continue if another handler fails.
The run ends in `needs_attention` after reporting finishes.

```yaml
    on_failure:
      report:
        type: action
        action: jira.report_failure
        inputs: {connection: jira}
        input_refs:
          issue: inputs.work_item.id
          run_id: failure.run_id
          failed_step: failure.step
          status: failure.status
          summary: failure.summary
          error: failure.error
          attempt_id: failure.attempt_id
          diagnostics: failure.diagnostics
          artifacts: failure.artifacts
          failures: failure.failures
      open_question:
        type: action
        action: jira.transition
        inputs: {connection: jira, status: Open Question}
        input_refs: {issue: inputs.work_item.id}
```

Handlers do not run for a successful workflow or an explicit cancellation.
They use the same runner and project credentials as normal steps. A retried
failure report uses the workflow run ID to avoid a duplicate Jira comment.

Validate the project before adding it in the browser. An online runner assigned
to the project fetches its repository and returns the workflow configuration:

```sh
uv run aorch validate path/to/project
uv run aorch config show path/to/project --sources
uv run aorch actions list
```

Imported project configuration uses `version: 3`; the engine normalizes it to
the same execution format used by existing runners. New runners use protocol
version 5 and advertise supported built-in action contracts during registration.

Add and remove projects through the browser. The engine stores its project
registry and run history in SQLite; `engine.yaml` contains engine settings only.
Workflow definitions remain in each project repository. The browser's Refresh
action fetches the configured ref and displays the discovered workflows. A run
starts from the commit shown for that project.

## Ask an agent to build a workflow

Describe the result you want and give the agent enough information to choose
the step types and boundaries. Include:

1. The workflow name and what starts it, manual or periodic with an interval.
2. The outcome each step must produce, including filenames and output fields.
3. Which steps depend on earlier results and what should happen when a step
   fails or a condition is false.
4. Whether an AI step may read, edit, or run commands, and which profile or
   adapter it should use.
5. Whether the workflow may commit, push, comment, publish, or otherwise make
   external changes. State the destination and desired commit message.
6. How to validate the finished `.aorch/config.yaml` and referenced files.

For example: “Create a manual `analyze` workflow. Use Claude to inspect the
repository and write one Markdown report. Then run the project tests. Only if
they pass, commit the report on a feature branch and open a pull request to
`main`. The PR description should summarize the report and list the files
changed. Keep the AI step read-only except for writing the report. Put its
prompt under `.aorch/prompts`, reference it from the config, and validate the
project.”

Treat commit, push, and other external writes as explicit workflow steps. The
AI prompt should describe the work, while a separate dependent action should
perform the publication. This makes the run history show which step changed
files and which step published them.


## Next steps

See [configuration](configuration.md) for imports, inputs and triggers,
[profiles and actions](profiles-and-actions.md) for runtime permissions and action
contracts, and the [maintained examples](../examples/public-mvp/README.md) for
review and coding workflows.
