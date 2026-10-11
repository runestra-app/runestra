"""Export editor schemas from the configuration and action contracts."""

import json
from copy import deepcopy
from pathlib import Path

from aorch.config.models import ProjectConfig
from aorch.config.service import EngineConfig, RunnerConfig
from aorch.extensions.schemas import ACTION_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "schemas"
SECTIONS = {
    "profiles": "AgentProfile",
    "actions": "ActionDefinition",
    "connections": "JiraConnection",
    "workflows": "Workflow",
}
SECRET_FIELDS = {
    "brevo_api_key",
    "google_client_secret",
    "github_client_secret",
    "webhook_triggers",
}


def set_metadata(schema: dict, name: str) -> dict:
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runestra:schema:{name}"
    return schema


def export() -> dict[str, dict]:
    project = ProjectConfig.model_json_schema()
    definitions = deepcopy(project["$defs"])
    definitions["AgentProfile"]["properties"]["tools"]["items"]["enum"] = [
        "read",
        "write",
        "command",
    ]
    outputs = {}

    for version in (1, 2):
        schema = deepcopy(project)
        schema["$defs"] = deepcopy(definitions)
        schema["properties"]["version"] = {"type": "integer", "const": version}
        outputs[f"project-v{version}.schema.json"] = set_metadata(
            schema, f"project-v{version}"
        )

    fragment_properties = {
        "imports": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        **{
            section: {
                "type": "object",
                "additionalProperties": {"$ref": f"#/$defs/{definition}"},
            }
            for section, definition in SECTIONS.items()
        },
    }
    outputs["project-v3-import.schema.json"] = set_metadata(
        {
            "$defs": definitions,
            "type": "object",
            "properties": fragment_properties,
            "additionalProperties": False,
        },
        "project-v3-import",
    )
    outputs["project-v3.schema.json"] = set_metadata(
        {
            "$defs": definitions,
            "type": "object",
            "properties": {
                "version": {"type": "integer", "const": 3},
                "name": {"type": "string", "minLength": 1},
                **fragment_properties,
            },
            "required": ["version", "name"],
            "additionalProperties": False,
        },
        "project-v3",
    )

    engine = EngineConfig.model_json_schema()
    for field in SECRET_FIELDS:
        engine["properties"].pop(field, None)
    outputs["engine.schema.json"] = set_metadata(engine, "engine")
    outputs["runner.schema.json"] = set_metadata(
        RunnerConfig.model_json_schema(), "runner"
    )
    outputs["runner.schema.json"]["properties"]["adapters"]["items"]["enum"] = [
        "action",
        "shell",
        "codex",
        "claude",
        "openai",
    ]
    outputs["runner.schema.json"]["properties"]["api_credentials"][
        "propertyNames"
    ] = {"enum": ["codex", "claude", "openai"]}
    outputs["runner.schema.json"]["properties"]["devices"] = {
        "type": "object",
        "additionalProperties": {
            "type": "object",
            "properties": {
                "platform": {"enum": ["ios", "android"]},
                "id": {"type": "string", "minLength": 1},
                "emulator": {"type": "string"},
                "avd": {"type": "string"},
            },
            "additionalProperties": {"type": "string"},
        },
    }

    outputs["secrets.schema.json"] = set_metadata(
        {
            "type": "object",
            "properties": {
                "brevo_api_key": {"type": "string"},
                "google_client_secret": {"type": "string"},
                "github_client_secret": {"type": "string"},
                "webhook_triggers": {
                    "type": "object",
                    "propertyNames": {"pattern": "^[A-Za-z0-9_-]{1,64}$"},
                    "additionalProperties": {
                        "type": "object",
                        "required": [
                            "provider",
                            "secret",
                            "project",
                            "workflow",
                            "input_mapping",
                        ],
                        "properties": {
                            "provider": {"enum": ["github", "hmac"]},
                            "secret": {"type": "string", "minLength": 32},
                            "project": {"type": "string", "minLength": 1},
                            "workflow": {"type": "string", "minLength": 1},
                            "input_mapping": {
                                "type": "object",
                                "minProperties": 1,
                                "additionalProperties": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                            },
                            "repository": {
                                "type": "string",
                                "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
                            },
                            "event": {"type": "string", "default": "issues"},
                            "actions": {
                                "type": "array",
                                "default": ["opened"],
                            },
                        },
                        "allOf": [
                            {
                                "if": {
                                    "properties": {
                                        "provider": {"const": "github"}
                                    },
                                    "required": ["provider"],
                                },
                                "then": {"required": ["repository"]},
                            }
                        ],
                        "additionalProperties": True,
                    },
                },
            },
            "additionalProperties": False,
        },
        "secrets",
    )

    outputs["actions.schema.json"] = set_metadata(
        {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "contract_version": {"type": "integer", "minimum": 1},
                    "description": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
                "required": [
                    "contract_version",
                    "description",
                    "input_schema",
                    "output_schema",
                ],
            },
        },
        "actions",
    )
    return outputs


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    generated = export()
    action_catalog = {
        name: {
            key: value
            for key, value in descriptor.items()
            if key != "handler"
        }
        for name, descriptor in sorted(ACTION_REGISTRY.items())
    }
    generated["actions.json"] = action_catalog
    for name, value in generated.items():
        path = OUTPUT / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
