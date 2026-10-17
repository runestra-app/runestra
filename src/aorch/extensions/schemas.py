"""Input contracts for built-in extension actions."""

from jsonschema import validate
from jsonschema.exceptions import ValidationError as SchemaValidationError

from aorch.contracts import StepResult


def object_schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1}
COMMANDS = {
    "type": "array",
    "minItems": 1,
    "items": {"type": "array", "minItems": 1, "items": {"type": "string"}},
}
SCHEMAS = {
    "github.discover": object_schema(
        {
            "connection": TEXT,
            "repository": TEXT,
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "labels": {"type": "array", "items": TEXT},
            "assignee": TEXT,
        },
        ("connection", "repository"),
    ),
    "github.get": object_schema(
        {"connection": TEXT, "repository": TEXT, "issue": {"type": "integer", "minimum": 1}},
        ("connection", "repository", "issue"),
    ),
    "jira.discover": object_schema(
        {
            "connection": TEXT,
            "jql": TEXT,
            "assignee": TEXT,
            "status": TEXT,
            "project": TEXT,
        },
        ("connection",),
    ),
    "jira.comment": object_schema(
        {
            "connection": TEXT,
            "issue": TEXT,
            "ambiguities": {
                "type": "array",
                "minItems": 1,
                "items": object_schema(
                    {"id": TEXT, "question": TEXT}, ("id", "question")
                ),
            },
        },
        ("connection", "issue", "ambiguities"),
    ),
    "jira.report_failure": object_schema(
        {
            "connection": TEXT,
            "issue": TEXT,
            "run_id": TEXT,
            "failed_step": TEXT,
            "status": TEXT,
            "summary": TEXT,
            "error": TEXT,
            "attempt_id": TEXT,
            "diagnostics": {"type": "object"},
            "artifacts": {"type": "array", "items": {"type": "object"}},
            "failures": {"type": "array", "items": {"type": "object"}},
        },
        ("connection", "issue", "run_id", "failed_step", "status", "summary", "error", "attempt_id"),
    ),
    "jira.transition": object_schema(
        {"connection": TEXT, "issue": TEXT, "status": TEXT},
        ("connection", "issue", "status"),
    ),
    "git.prepare": object_schema(
        {"repository": TEXT, "item_id": TEXT, "base": TEXT}, ("repository",)
    ),
    "git.branch": object_schema({}),
    "git.verify": object_schema(
        {
            "commands": COMMANDS,
            "paths": {"type": "array", "minItems": 1, "items": TEXT},
            "only_paths": {"type": "boolean"},
        },
        ("commands",),
    ),
    "git.commit": object_schema(
        {
            "message": TEXT,
            "paths": {"type": "array", "minItems": 1, "items": TEXT},
            "approval_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
        ("message",),
    ),
    "git.push": object_schema({"remote": TEXT}),
    "git.pr": object_schema(
        {"repository": TEXT, "base": TEXT, "title": TEXT, "body": TEXT, "remote": TEXT},
        ("repository", "base", "title", "body"),
    ),
    "coding.repair": object_schema(
        {
            "implement_profile": TEXT,
            "implement_prompt": TEXT,
            "review_profile": TEXT,
            "review_prompt": TEXT,
            "test_commands": COMMANDS,
            "max_iterations": {"type": "integer", "minimum": 1, "maximum": 20},
            "skills": {"type": "array", "items": TEXT},
        },
        (
            "implement_profile",
            "implement_prompt",
            "review_profile",
            "review_prompt",
            "test_commands",
        ),
    ),
    "device.test": object_schema(
        {"device": TEXT, "commands": COMMANDS}, ("device", "commands")
    ),
    "core.needs_input": object_schema(
        {
            "summary": {"type": "string"},
            "ambiguities": {"type": "array", "items": {"type": "object"}},
        }
    ),
}

SCHEMAS["github.open_pr"] = SCHEMAS["git.pr"]

ACTION_CONTRACT_VERSIONS = {
    name: (2 if name in {"git.branch", "git.push", "git.pr", "github.open_pr", "github.discover", "github.get"} else 1)
    for name in SCHEMAS
}
ACTION_CONTRACT_VERSIONS.update({"git.verify": 3, "git.commit": 3, "coding.repair": 2})

ACTION_OUTPUT_SCHEMAS = {
    "github.discover": object_schema(
        {"work_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": TEXT, "title": TEXT, "description": {"type": "string"}, "url": TEXT, "labels": {"type": "array", "items": TEXT}, "status": TEXT, "updated": TEXT},
            "required": ["id"],
            "additionalProperties": False,
        }}},
        ("work_items",),
    ),
    "github.get": object_schema({"work_item": {
        "type": "object",
        "properties": {"id": TEXT, "title": TEXT, "description": {"type": "string"}, "url": TEXT, "labels": {"type": "array", "items": TEXT}, "status": TEXT, "updated": TEXT},
        "required": ["id"],
        "additionalProperties": False,
    }}, ("work_item",)),
    "git.branch": object_schema(
        {
            "repository": TEXT,
            "branch": TEXT,
            "starting_commit": TEXT,
            "mode": {"type": "string", "enum": ["checkout"]},
            "owner": TEXT,
        },
        ("repository", "branch", "starting_commit", "mode", "owner"),
    ),
    "git.verify": object_schema(
        {
            "verified": {"type": "boolean", "const": True},
            "fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
        ("verified", "fingerprint"),
    ),
    "coding.repair": object_schema(
        {
            "verified": {"type": "boolean", "const": True},
            "fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
        ("verified", "fingerprint"),
    ),
    "git.commit": object_schema({"commit": TEXT, "branch": TEXT}, ("commit", "branch")),
    "git.push": object_schema(
        {"remote": TEXT, "branch": TEXT, "commit": TEXT},
        ("remote", "branch", "commit"),
    ),
    "git.pr": object_schema(
        {
            "pull_request": TEXT,
            "number": {"type": "integer", "minimum": 1},
            "branch": TEXT,
            "commit": TEXT,
            "head_commit": TEXT,
            "base": TEXT,
            "external_effects": {
                "type": "array",
                "minItems": 1,
                "items": object_schema(
                    {"key": TEXT, "kind": {"const": "git.pr"}, "provider_id": TEXT},
                    ("key", "kind", "provider_id"),
                ),
            },
        },
        ("pull_request", "number", "branch", "commit", "head_commit", "base", "external_effects"),
    ),
    "github.open_pr": object_schema(
        {
            "pull_request": TEXT,
            "number": {"type": "integer", "minimum": 1},
            "branch": TEXT,
            "commit": TEXT,
            "head_commit": TEXT,
            "base": TEXT,
            "external_effects": {
                "type": "array",
                "minItems": 1,
                "items": object_schema(
                    {"key": TEXT, "kind": {"const": "git.pr"}, "provider_id": TEXT},
                    ("key", "kind", "provider_id"),
                ),
            },
        },
        ("pull_request", "number", "branch", "commit", "head_commit", "base", "external_effects"),
    ),
}

ACTION_DESCRIPTIONS = {
    "github.discover": "Find GitHub issues for a workflow.",
    "github.get": "Read one GitHub issue.",
    "jira.discover": "Find Jira work items for a workflow.",
    "jira.comment": "Add a reconciled comment to a Jira issue.",
    "jira.report_failure": "Record a workflow failure on its Jira work item.",
    "jira.transition": "Transition a Jira issue to a workflow status.",
    "git.prepare": "Create an isolated worktree for another configured repository.",
    "git.branch": "Create or resume a run branch in the pinned project checkout.",
    "git.verify": "Run checks and record the verified workspace fingerprint.",
    "git.commit": "Commit verified changes, optionally limited to declared paths.",
    "git.push": "Push the verified commit and confirm the remote ref.",
    "git.pr": "Create or reconcile a GitHub pull request.",
    "github.open_pr": "Create or reconcile a GitHub pull request.",
    "coding.repair": "Iterate implementation, review and tests in the run workspace.",
    "device.test": "Run commands on a configured device and collect artifacts.",
    "core.needs_input": "Stop the workflow and request human input.",
}

# This catalog is the shared contract used by validation, scheduling, CLI and dispatch.
ACTION_REGISTRY = {
    name: {
        "name": name,
        "aliases": ["git.pr"] if name == "github.open_pr" else [],
        "contract_version": ACTION_CONTRACT_VERSIONS[name],
        "input_schema": schema,
        "output_schema": ACTION_OUTPUT_SCHEMAS.get(
            name, {"type": "object", "additionalProperties": True}
        ),
        "description": ACTION_DESCRIPTIONS[name],
        "capability": "action",
        "workspace": name.startswith("git.") or name == "coding.repair",
        "external_effect": name
        in {"jira.comment", "jira.report_failure", "jira.transition", "git.push", "git.pr", "github.open_pr"},
        "handler": (
            "core" if name == "core.needs_input" else
            "jira" if name.startswith("jira.") else
            "git" if name.startswith("git.") or name in {"github.open_pr", "coding.repair"} else
            "github" if name.startswith("github.") else
            "device"
        ),
    }
    for name, schema in SCHEMAS.items()
}

SUPPORTED_ACTION_CONTRACTS = {
    name: descriptor["contract_version"]
    for name, descriptor in ACTION_REGISTRY.items()
}


def required_action_version(name: str, inputs: dict, references: dict) -> int:
    version = ACTION_REGISTRY[name]["contract_version"]
    declared = set(inputs) | set(references)
    if name == "git.verify":
        if "fingerprint" in references:
            return 3
        if not ({"paths", "only_paths"} & declared):
            return 1
        return 2
    if name == "git.commit":
        if "approval_fingerprint" in declared:
            return 3
        if "paths" not in declared:
            return 1
        return 2
    return version


def validate_action_result(name: str, result: StepResult, version: int) -> None:
    """Validate the versioned output portion of a successful built-in action."""
    if result.status != "succeeded" or version < 2 or name not in ACTION_REGISTRY:
        return
    try:
        validate(result.outputs, ACTION_REGISTRY[name]["output_schema"])
    except SchemaValidationError as error:
        raise ValueError(f"Invalid {name} v{version} result: {error.message}") from error


def action_requirements(configuration: dict) -> dict[str, dict[str, int]]:
    """Collect the minimum built-in action contracts used by each workflow."""
    requirements = {}
    project_actions = configuration.get("actions", {})
    for workflow_name, workflow in configuration.get("workflows", {}).items():
        workflow_requirements = {}
        for step in [*workflow.get("steps", {}).values(), *workflow.get("on_failure", {}).values()]:
            name = step.get("action")
            if name and name not in project_actions and name in ACTION_REGISTRY:
                version = required_action_version(name, step.get("inputs", {}), step.get("input_refs", {}))
                workflow_requirements[name] = max(workflow_requirements.get(name, 0), version)
        for step in workflow.get("steps", {}).values():
            reference = step.get("content_ref", "") if step.get("type") == "approval" else ""
            parts = reference.split(".")
            if len(parts) == 4 and parts[0] == "steps" and parts[2:] == ["outputs", "fingerprint"]:
                source = workflow.get("steps", {}).get(parts[1], {})
                if source.get("type") == "action" and source.get("action") == "git.verify":
                    workflow_requirements["git.verify"] = max(workflow_requirements.get("git.verify", 0), 3)
        for step in workflow.get("steps", {}).values():
            reference = step.get("content_ref", "") if step.get("type") == "approval" else ""
            parts = reference.split(".")
            if len(parts) == 4 and parts[0] == "steps" and parts[2:] == ["outputs", "fingerprint"]:
                source = workflow.get("steps", {}).get(parts[1], {})
                if source.get("type") == "action" and source.get("action") == "git.verify":
                    workflow_requirements["git.verify"] = max(workflow_requirements.get("git.verify", 0), 3)
        if workflow_requirements:
            requirements[workflow_name] = workflow_requirements
    return requirements


def validate_action(name, inputs, references):
    if name not in ACTION_REGISTRY:
        raise ValueError(f"Unknown action: {name}")
    schema = ACTION_REGISTRY[name]["input_schema"]
    required, allowed = set(schema["required"]), set(schema["properties"])
    declared = set(inputs) | set(references)
    if declared - allowed or required - declared:
        raise ValueError(
            f"Invalid {name} inputs; missing={sorted(required - declared)}, unknown={sorted(declared - allowed)}"
        )
    if name == "jira.discover" and not (
        "jql" in declared or {"assignee", "status"}.issubset(declared)
    ):
        raise ValueError("Jira discovery requires jql or assignee and status")
    literals = {key: value for key, value in inputs.items() if key not in references}
    validate(literals, {**schema, "required": []})
