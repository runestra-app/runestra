"""Safe loading and content-addressed configuration snapshots."""

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from aorch.extensions.schemas import action_requirements

from .loader import load_project
from .yaml import ConfigurationError
from .yaml import UniqueLoader as UniqueLoader


def configuration_path(path: Path) -> Path:
    return path / ".aorch" / "config.yaml" if path.is_dir() else path


def snapshot(path: Path) -> dict:
    try:
        is_project_directory = path.is_dir()
        if is_project_directory:
            project_root = path.resolve()
        elif path.parent.name == ".aorch":
            project_root = path.parent.parent.resolve()
        else:
            project_root = path.parent.resolve()
        source = configuration_path(path)
        aorch_directory = path / ".aorch" if is_project_directory else path.parent
        if aorch_directory.name == ".aorch" and aorch_directory.is_symlink():
            raise ConfigurationError("Configuration directory cannot be a symbolic link")
        if source.is_symlink():
            raise ConfigurationError("Configuration file cannot be a symbolic link")
        source = source.resolve()
        if not source.is_relative_to(project_root):
            raise ConfigurationError("Configuration file escapes project root")
        config, config_sources = load_project(source)
        files = {}
        root = source.parent
        for workflow_name, workflow in config.workflows.items():
            for step_name, step in workflow.steps.items():
                references = ([step.prompt] if step.prompt is not None else []) + step.skills
                if step.action in config.actions:
                    definition = config.actions[step.action]
                    references += definition.files
                    for argument in definition.argv:
                        if argument.startswith("@package/"):
                            references.append(argument.removeprefix("@package/"))
                if step.action == "coding.repair":
                    references += [
                        step.inputs["implement_prompt"],
                        step.inputs["review_prompt"],
                        *step.inputs.get("skills", []),
                    ]
                for reference in references:
                    target = (root / reference).resolve()
                    field = f"workflows.{workflow_name}.steps.{step_name}"
                    if not target.is_relative_to(root):
                        raise ConfigurationError(
                            f"{field}: reference escapes configuration root: {reference}"
                        )
                    if not target.exists():
                        raise ConfigurationError(
                            f"{field}: missing reference: {reference}"
                        )
                    targets = sorted(target.rglob("*")) if target.is_dir() else [target]
                    if target.is_dir() and not (target / "SKILL.md").is_file():
                        raise ConfigurationError(
                            f"{field}: skill directory requires SKILL.md: {reference}"
                        )
                    for item in targets:
                        resolved = item.resolve()
                        if not resolved.is_relative_to(root):
                            raise ConfigurationError(
                                f"{field}: reference contains escaping symlink: {item}"
                            )
                        if item.is_file():
                            content = item.read_text()
                            files[item.relative_to(root).as_posix()] = {
                                "content": content,
                                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                            }
        payload = {
            "protocol_version": "1",
            "configuration": config.model_dump(mode="json"),
            "files": files,
        }
        requirements = action_requirements(payload["configuration"])
        if requirements:
            payload["action_contracts"] = requirements
        if config_sources:
            payload["config_source_version"] = 3
            payload["config_sources"] = {
                name: {
                    "content": content,
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
                for name, content in sorted(config_sources.items())
            }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return {**payload, "sha256": hashlib.sha256(encoded).hexdigest()}
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, entry['loc'])) or '<root>'}: {entry['msg']}"
            for entry in error.errors()
        )
        raise ConfigurationError(f"{source}: {details}") from error
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"{source}: {error}") from error
