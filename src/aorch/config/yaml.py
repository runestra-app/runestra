"""Safe YAML loading with duplicate-key rejection."""

import yaml


class ConfigurationError(ValueError):
    """Invalid or unsafe project configuration."""


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate and non-string mapping keys."""


def _mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ConfigurationError(
                f"YAML mapping keys must be strings at line {key_node.start_mark.line + 1}"
            )
        if key in mapping:
            raise ConfigurationError(
                f"Duplicate YAML key '{key}' at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
