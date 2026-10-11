"""Resolve modular project configuration into a validated execution model."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from .yaml import ConfigurationError, UniqueLoader

SECTIONS = ("profiles", "actions", "connections", "workflows")
MAX_FILES = 128
MAX_DEPTH = 16
MAX_BYTES = 2 * 1024 * 1024


def _read_yaml(source: Path) -> tuple[dict, str]:
    try:
        content = source.read_text(encoding="utf-8")
        loaded = yaml.load(content, Loader=UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        raise ConfigurationError(f"{source}: {error}") from error
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"{source}: expected a YAML mapping")
    if any(not isinstance(key, str) for key in loaded):
        raise ConfigurationError(f"{source}: YAML mapping keys must be strings")
    return loaded, content


def _line_for_key(content: str, section: str, name: str) -> int | None:
    try:
        node = yaml.compose(content, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            return None
        for key_node, value_node in node.value:
            if key_node.value != section or not isinstance(value_node, yaml.MappingNode):
                continue
            for item_key, _ in value_node.value:
                if item_key.value == name:
                    return item_key.start_mark.line + 1
    except (yaml.YAMLError, AttributeError):
        return None
    return None


def _line_for_path(content: str, location: list[object]) -> int | None:
    try:
        node = yaml.compose(content, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            return None
        parts = list(location)
        keys = {key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)}
        if parts and parts[0] in keys:
            parts.pop(0)
        for part in parts:
            if not isinstance(node, yaml.MappingNode):
                return None
            child = next(
                (value for key, value in node.value if str(key.value) == str(part)),
                None,
            )
            if child is None:
                return None
            node = child
        return node.start_mark.line + 1
    except (yaml.YAMLError, AttributeError):
        return None


def _relative(root: Path, source: Path) -> str:
    return source.relative_to(root).as_posix()


def load_project(source: Path):
    """Load legacy single-file projects or resolve a version-three import graph."""
    from .models import ProjectConfig

    configured = source
    root = configured.parent.resolve()
    source = configured.resolve()
    if not source.is_relative_to(root):
        raise ConfigurationError(f"{configured}: configuration file escapes its root")
    document, content = _read_yaml(source)
    version = document.get("version")
    if version != 3:
        return ProjectConfig.model_validate(document), {}

    collected: dict[str, dict] = {section: {} for section in SECTIONS}
    origins: dict[str, dict[str, Path]] = {section: {} for section in SECTIONS}
    source_contents: dict[str, str] = {}
    visiting: list[Path] = []
    visited: set[Path] = set()
    total_bytes = 0

    def visit(current: Path, depth: int, *, main: bool = False) -> None:
        nonlocal total_bytes
        current = current.resolve()
        if not current.is_relative_to(root):
            raise ConfigurationError(
                f"{current}: import escapes configuration root {root}"
            )
        if current in visiting:
            cycle = " -> ".join(_relative(root, item) for item in [*visiting, current])
            raise ConfigurationError(f"Import cycle: {cycle}")
        if current in visited:
            return
        if depth > MAX_DEPTH:
            raise ConfigurationError(f"{current}: import depth exceeds {MAX_DEPTH}")
        if len(visited) + len(visiting) >= MAX_FILES:
            raise ConfigurationError(f"{current}: import count exceeds {MAX_FILES}")

        data, yaml_text = (document, content) if current == source else _read_yaml(current)
        total_bytes += len(yaml_text.encode("utf-8"))
        if total_bytes > MAX_BYTES:
            raise ConfigurationError(f"{current}: imported YAML exceeds {MAX_BYTES} bytes")

        allowed = {"imports", *SECTIONS}
        if main:
            allowed.update({"version", "name"})
            if data.get("version") != 3:
                raise ConfigurationError(f"{current}: imported configuration requires version 3")
            if not isinstance(data.get("name"), str) or not data["name"].strip():
                raise ConfigurationError(f"{current}: version 3 requires a nonempty name")
        extra = set(data) - allowed
        if extra:
            raise ConfigurationError(
                f"{current}: unsupported configuration fields: {', '.join(sorted(extra))}"
            )

        imports = data.get("imports", [])
        if not isinstance(imports, list) or any(
            not isinstance(item, str) or not item.strip() for item in imports
        ):
            raise ConfigurationError(f"{current}: imports must be a list of file paths")

        visiting.append(current)
        for reference in imports:
            relative = PurePosixPath(reference)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in reference
                or not relative.parts
            ):
                raise ConfigurationError(f"{current}: unsafe import path: {reference}")
            target = (current.parent / Path(*relative.parts)).resolve()
            if target.suffix.lower() not in {".yaml", ".yml"}:
                raise ConfigurationError(f"{current}: import must name a YAML file: {reference}")
            if not target.is_file():
                raise ConfigurationError(f"{current}: imported file does not exist: {reference}")
            visit(target, depth + 1)

        for section in SECTIONS:
            definitions = data.get(section, {})
            if not isinstance(definitions, dict):
                raise ConfigurationError(f"{current}: {section} must be a mapping")
            for name, definition in definitions.items():
                if not isinstance(name, str) or not name:
                    raise ConfigurationError(f"{current}: {section} names must be nonempty strings")
                if not isinstance(definition, dict):
                    raise ConfigurationError(f"{current}: {section}.{name} must be a mapping")
                if section == "actions":
                    from aorch.extensions.schemas import ACTION_REGISTRY

                    if name in ACTION_REGISTRY:
                        raise ConfigurationError(
                            f"{current}: project action '{name}' uses a reserved built-in action name"
                        )
                if name in collected[section]:
                    previous = origins[section][name]
                    previous_line = _line_for_key(
                        source_contents[_relative(root, previous)], section, name
                    )
                    current_line = _line_for_key(yaml_text, section, name)
                    previous_location = _relative(root, previous)
                    current_location = _relative(root, current)
                    if previous_line is not None:
                        previous_location += f":{previous_line}"
                    if current_line is not None:
                        current_location += f":{current_line}"
                    raise ConfigurationError(
                        f"Duplicate {section[:-1]} '{name}': {previous_location}; {current_location}"
                    )
                collected[section][name] = definition
                origins[section][name] = current

        source_contents[_relative(root, current)] = yaml_text
        visiting.pop()
        visited.add(current)

    visit(source, 0, main=True)

    execution = {
        "version": 2,
        "name": document["name"],
        **collected,
    }
    try:
        config = ProjectConfig.model_validate(execution)
    except ValidationError as error:
        details = []
        for entry in error.errors():
            location = list(entry["loc"])
            section = str(location[0]) if location else ""
            owner = origins.get(section, {}).get(str(location[1])) if len(location) > 1 else None
            if owner is None:
                message = entry["msg"]
                owner = next(
                    (
                        candidate
                        for candidate_section, names in origins.items()
                        for candidate_name, candidate in names.items()
                        if f"{candidate_section}.{candidate_name}" in message
                    ),
                    None,
                )
            prefix = _relative(root, owner) if owner else _relative(root, source)
            owner_content = source_contents.get(prefix)
            line = _line_for_path(owner_content, location) if owner_content else None
            if line is not None:
                prefix += f":{line}"
            details.append(f"{prefix}: {'.'.join(map(str, location)) or '<root>'}: {entry['msg']}")
        raise ConfigurationError("; ".join(details)) from error

    return config, source_contents
