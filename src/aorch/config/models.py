"""Strict version-one project configuration."""

import ast
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, model_validator

from aorch.contracts import Contract

from .conditions import parse_condition
from .schedule import next_cron_time, validate_cron


def check_local_schema_references(schema):
    """Reject schema identifiers and references that could trigger network access."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(value, str) or not value.startswith("#")
            ):
                raise ValueError("Schema references must be local fragments")
            if key == "$id" and (
                not isinstance(value, str) or (value and not value.startswith("#"))
            ):
                raise ValueError("Schema IDs must be local fragments")
            check_local_schema_references(value)
    elif isinstance(schema, list):
        for value in schema:
            check_local_schema_references(value)


class AgentProfile(Contract):
    adapter: Literal["codex", "claude", "openai"]
    authentication: Literal["local", "api"]
    model: str | None = None
    credential_env: str | None = None
    tools: list[str] = Field(default_factory=lambda: ["read", "write", "command"])
    max_iterations: int = Field(default=12, ge=1, le=100)
    max_output_tokens: int = Field(default=4096, ge=128)
    max_total_tokens: int = Field(default=50000, ge=128)
    max_budget_usd: float = Field(default=1, gt=0)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    sandbox: Literal["read-only", "workspace-write"] = "workspace-write"

    @model_validator(mode="after")
    def check_authentication(self):
        if self.credential_env and not self.credential_env.replace("_", "").isalnum():
            raise ValueError("Credential references must be environment variable names")
        if self.adapter == "openai" and self.authentication != "api":
            raise ValueError("OpenAI runtime requires API authentication")
        if not set(self.tools).issubset({"read", "write", "command"}):
            raise ValueError("Unknown agent tool")
        if self.sandbox == "read-only" and "write" in self.tools:
            raise ValueError("Read-only profile cannot allow writing")
        return self


class Repeat(Contract):
    max_iterations: int = Field(ge=1, le=100)
    until: str

    @model_validator(mode="after")
    def check_condition(self):
        parse_condition(self.until)
        return self


class Step(Contract):
    type: Literal["action", "shell", "agent", "approval"]
    needs: list[str] = Field(default_factory=list)
    when: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    action: str | None = None
    input_refs: dict[str, str] = Field(default_factory=dict)
    argv: list[str] | None = None
    command: str | None = None
    profile: str | None = None
    prompt: str | None = None
    prompt_text: str | None = None
    skills: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=300, ge=1)
    result_schema: dict[str, Any] | None = None
    repeat: Repeat | None = None
    approvers: list[Literal["owner", "admin"]] = Field(default_factory=lambda: ["owner", "admin"])
    approval_timeout_seconds: int = Field(default=86400, ge=60, le=604800)
    summary_ref: str | None = None
    content_ref: str | None = None

    @model_validator(mode="after")
    def check_execution(self):
        if self.type == "approval":
            if not self.needs:
                raise ValueError("Approval requires at least one dependency")
            if not self.summary_ref or not self.content_ref:
                raise ValueError("Approval requires summary_ref and content_ref")
            if (
                self.action
                or self.argv
                or self.command
                or self.profile
                or self.prompt
                or self.prompt_text is not None
            ):
                raise ValueError("Approval cannot define an executor")
            for reference in (self.summary_ref, self.content_ref):
                tree = parse_condition(reference)
                if (
                    not isinstance(tree.body, ast.Attribute)
                    or not isinstance(tree.body.value, (ast.Name, ast.Attribute))
                ):
                    raise ValueError("Approval references must be dotted paths")  # noqa: TRY004
                root = tree.body
                while isinstance(root, ast.Attribute):
                    root = root.value
                if root.id not in {"inputs", "steps"}:
                    raise ValueError("Approval references must start at inputs or steps")
            forbidden = ()
        elif self.type == "action":
            if bool(self.argv) == bool(self.action):
                raise ValueError("Action requires either argv or a named action")
            if self.argv and not self.argv[0]:
                raise ValueError("Action argv must start with an executable")
            forbidden = (self.command, self.profile, self.prompt, self.prompt_text)
        elif self.type == "shell":
            if not self.command:
                raise ValueError("Shell requires command")
            forbidden = (
                self.argv,
                self.action,
                self.profile,
                self.prompt,
                self.prompt_text,
            )
        else:
            if not self.profile:
                raise ValueError("Agent requires a profile")
            if (self.prompt is None) == (self.prompt_text is None):
                raise ValueError("Agent requires exactly one of prompt or prompt_text")
            if self.prompt is not None and not self.prompt.strip():
                raise ValueError("Agent prompt reference cannot be empty")
            if self.prompt_text is not None and not self.prompt_text.strip():
                raise ValueError("Agent prompt_text cannot be empty")
            forbidden = (self.argv, self.action, self.command)
        if any(value is not None for value in forbidden):
            raise ValueError("Fields from another executor type are forbidden")
        for reference in self.input_refs.values():
            tree = parse_condition(reference)
            nodes = list(ast.walk(tree))
            if (
                any(
                    not isinstance(
                        node, (ast.Expression, ast.Name, ast.Attribute, ast.Load)
                    )
                    for node in nodes
                )
                or not isinstance(tree.body, ast.Attribute)
                or not isinstance(tree.body.value, (ast.Name, ast.Attribute))
            ):
                raise ValueError("Input references must be dotted paths")
            root = tree.body
            while isinstance(root, ast.Attribute):
                root = root.value
            if not isinstance(root, ast.Name) or root.id not in {"inputs", "steps"}:
                raise ValueError("Input references must start at inputs or steps")
        if self.when:
            parse_condition(self.when)
        if self.result_schema is not None:
            try:
                check_local_schema_references(self.result_schema)
                Draft202012Validator.check_schema(self.result_schema)
            except (SchemaError, ValueError) as error:
                message = error.message if isinstance(error, SchemaError) else str(error)
                raise ValueError(f"Invalid result schema: {message}") from error
        return self


class Discovery(Contract):
    target_workflow: str
    source: str = Field(min_length=1)


class PeriodicTrigger(Contract):
    type: Literal["periodic", "cron"] = "periodic"
    interval_seconds: int | None = Field(default=None, ge=1)
    cron: str | None = None
    timezone: str = "UTC"
    enabled: bool = True

    @model_validator(mode="after")
    def check_schedule(self):
        if self.type == "periodic":
            if self.interval_seconds is None or self.cron is not None:
                raise ValueError("Periodic triggers require interval_seconds only")
        else:
            if not self.cron or self.interval_seconds is not None:
                raise ValueError("Cron triggers require cron only")
            validate_cron(self.cron, self.timezone)
        return self


class Workflow(Contract):
    discovery: Discovery | None = None
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    steps: dict[str, Step] = Field(min_length=1)
    max_active_runs: int = Field(default=1, ge=1)
    interval_seconds: int | None = Field(default=None, ge=1)
    triggers: list[PeriodicTrigger] = Field(default_factory=list)

    @property
    def periodic_interval(self):
        if self.triggers:
            return next(
                (trigger.interval_seconds for trigger in self.triggers if trigger.enabled and trigger.type == "periodic"),
                None,
            )
        return self.interval_seconds

    def next_periodic_due(self, after):
        trigger = next((item for item in self.triggers if item.enabled), None)
        if trigger and trigger.type == "cron":
            return next_cron_time(trigger.cron, trigger.timezone, after)
        interval = trigger.interval_seconds if trigger else self.interval_seconds
        return after + interval if interval else None

    @model_validator(mode="after")
    def check_graph(self):
        try:
            check_local_schema_references(self.input_schema)
            Draft202012Validator.check_schema(self.input_schema)
            if self.input_schema.get("type") != "object":
                raise ValueError("Workflow input_schema must describe an object")
        except (SchemaError, ValueError) as error:
            message = error.message if isinstance(error, SchemaError) else str(error)
            raise ValueError(f"Invalid workflow input_schema: {message}") from error
        if len(self.triggers) > 1:
            raise ValueError("A workflow can define at most one trigger")
        if self.triggers and self.interval_seconds is not None:
            raise ValueError("Use either interval_seconds or an explicit trigger, not both")
        if self.triggers and self.interval_seconds is not None:
            raise ValueError("Use either interval_seconds or an explicit trigger, not both")
        visited, active = set(), set()

        def visit(name):
            if name not in self.steps:
                raise ValueError(f"Unknown dependency: {name}")
            if name in active:
                raise ValueError(f"Dependency cycle at {name}")
            if name in visited:
                return
            active.add(name)
            for dependency in self.steps[name].needs:
                visit(dependency)
            active.remove(name)
            visited.add(name)

        for name, step in self.steps.items():
            if not name.isidentifier() or name.startswith("_"):
                raise ValueError(f"Invalid step identifier: {name}")
            visit(name)
            ancestors = set()

            def collect(key, ancestors=ancestors):
                for dependency in self.steps[key].needs:
                    if dependency not in ancestors:
                        ancestors.add(dependency)
                        collect(dependency)

            collect(name)
            expressions = [step.when, *step.input_refs.values()] + (
                [step.repeat.until] if step.repeat else []
            )
            if step.type == "approval":
                expressions.extend([step.summary_ref, step.content_ref])
            for expression in filter(None, expressions):
                for node in ast.walk(parse_condition(expression)):
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "steps"
                    ):
                        allowed = ancestors | (
                            {name}
                            if step.repeat and expression == step.repeat.until
                            else set()
                        )
                        if node.attr not in allowed:
                            raise ValueError(
                                f"Step {name} condition references unavailable step {node.attr}"
                            )
        return self


class ActionDefinition(Contract):
    argv: list[str] = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any]
    result_schema: dict[str, Any]

    @model_validator(mode="after")
    def check_schemas(self):
        for schema in (self.input_schema, self.result_schema):
            try:
                check_local_schema_references(schema)
                Draft202012Validator.check_schema(schema)
            except (SchemaError, ValueError) as error:
                message = error.message if isinstance(error, SchemaError) else str(error)
                raise ValueError(f"Invalid action schema: {message}") from error
        return self


class JiraConnection(Contract):
    url: str = Field(min_length=1)
    email: str | None = None
    token_env: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_connection(self):
        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Jira connection URL must be HTTPS without credentials, query, or fragment"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.token_env):
            raise ValueError("Jira token_env must be an environment variable name")
        return self


class ProjectConfig(Contract):
    version: Literal[1, 2]
    name: str = Field(min_length=1)
    actions: dict[str, ActionDefinition] = Field(default_factory=dict)
    connections: dict[str, JiraConnection] = Field(default_factory=dict)
    profiles: dict[str, AgentProfile] = Field(default_factory=dict)
    workflows: dict[str, Workflow] = Field(min_length=1)

    @model_validator(mode="after")
    def check_profiles(self):
        if self.version == 1 and any(
            workflow.triggers for workflow in self.workflows.values()
        ):
            raise ValueError("Explicit workflow triggers require configuration version 2")
        for workflow_name, workflow in self.workflows.items():
            if workflow.discovery:
                target = workflow.discovery.target_workflow
                if target not in self.workflows or target == workflow_name:
                    raise ValueError(f"Invalid discovery target: {target}")
                if self.workflows[target].discovery:
                    raise ValueError(
                        "Discovery cannot target another discovery workflow"
                    )
            for name, step in workflow.steps.items():
                approval_fingerprint = step.input_refs.get("approval_fingerprint")
                if approval_fingerprint is not None:
                    prefix = "steps."
                    suffix = ".outputs.content_fingerprint"
                    if not approval_fingerprint.startswith(prefix) or not approval_fingerprint.endswith(suffix):
                        raise ValueError("approval_fingerprint must reference an approval step")
                    approval_name = approval_fingerprint[len(prefix):-len(suffix)]
                    approval_step = workflow.steps.get(approval_name)
                    if step.action != "git.commit" or approval_step is None or approval_step.type != "approval":
                        raise ValueError("approval_fingerprint must reference an approval step")
                    if approval_name not in step.needs:
                        raise ValueError("git.commit must depend directly on its approval step")
                if step.action and step.action not in self.actions:
                    from aorch.extensions.schemas import validate_action

                    validate_action(step.action, step.inputs, step.input_refs)
                if step.action in {"jira.discover", "jira.comment", "jira.transition"}:
                    connection_name = step.inputs.get("connection")
                    if connection_name not in self.connections:
                        raise ValueError(
                            f"workflows.{workflow_name}.steps.{name}: Jira connection "
                            f"'{connection_name}' is not defined in project connections"
                        )
                if step.action == "coding.repair":
                    for role in ("implement", "review"):
                        if step.inputs.get(f"{role}_profile") not in self.profiles:
                            raise ValueError(f"Unknown repair profile: {role}")
                    if not step.inputs.get("test_commands"):
                        raise ValueError("Repair requires nonempty test_commands")
                if step.profile and step.profile not in self.profiles:
                    raise ValueError(
                        f"workflows.{workflow_name}.steps.{name}.profile: unknown profile {step.profile}"
                    )
        return self
