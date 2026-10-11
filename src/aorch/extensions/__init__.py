"""Explicit provider actions, outside the scheduler."""

from aorch.contracts import StepResult
from aorch.extensions.schemas import ACTION_REGISTRY

ACTIONS = frozenset(ACTION_REGISTRY)


async def execute_action(
    name, inputs, context, assignment, config, journal, cancelled, workspace
):
    if name not in ACTIONS:
        raise ValueError(f"Unknown action: {name}")
    handler = ACTION_REGISTRY[name]["handler"]
    if handler == "core":
        return StepResult(
            status="needs_input",
            summary=inputs.get("summary", "Work item requires clarification"),
            outputs=inputs,
        )
    if handler == "jira":
        from aorch.extensions.jira import jira_action

        project_connections = assignment["package"]["configuration"].get(
            "connections", {}
        )
        return await jira_action(
            name, inputs, project_connections, cancelled, workspace, config=config
        )
    if handler == "github":
        from aorch.extensions.github import github_action

        return await github_action(name, inputs, config, cancelled)
    if handler == "git":
        from aorch.extensions.git import git_action

        return await git_action(
            name, inputs, context, assignment, config, journal, cancelled, workspace
        )
    from aorch.extensions.devices import test_device

    if handler == "device":
        return await test_device(inputs, assignment, config, journal, cancelled, workspace)
    raise ValueError(f"Action {name} has unsupported handler {handler}")
