import copy
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aorch.cli import app
from aorch.config import ConfigurationError, snapshot
from aorch.config.conditions import evaluate_condition

ROOT = Path(__file__).resolve().parents[1]
BASE = {
    "version": 1,
    "name": "test",
    "workflows": {
        "work": {
            "steps": {
                "first": {"type": "action", "argv": ["echo", "hello"]},
                "second": {"type": "shell", "needs": ["first"], "command": "echo done"},
            }
        }
    },
}


def write(tmp_path, data):
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(data))
    return source


@pytest.mark.parametrize(
    "example", ["local-actions", "jira-coding", "document-analysis"]
)
def test_examples(example):
    path = ROOT / "examples" / example
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code == 0
    assert snapshot(path) == snapshot(path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(unknown=True),
        lambda d: d["workflows"]["work"]["steps"]["first"].update(needs=["second"]),
        lambda d: d["workflows"]["work"]["steps"]["second"].update(needs=["missing"]),
        lambda d: d["workflows"]["work"]["steps"]["second"].update(
            when="__import__('os')"
        ),
        lambda d: d["workflows"]["work"]["steps"]["first"].update(
            when="steps.second.status == 'succeeded'"
        ),
        lambda d: d["workflows"]["work"]["steps"].update(
            agent={"type": "agent", "profile": "missing", "prompt": "x.md"}
        ),
        lambda d: d["workflows"]["work"]["steps"]["first"].update(
            command="echo hidden"
        ),
    ],
)
def test_invalid_configuration(tmp_path, mutation):
    data = copy.deepcopy(BASE)
    mutation(data)
    source = write(tmp_path, data)
    with pytest.raises(ConfigurationError, match="config.yaml"):
        snapshot(source)


def test_duplicate_keys(tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text("version: 1\nversion: 1\n")
    with pytest.raises(ConfigurationError, match="Duplicate"):
        snapshot(source)


def test_reference_escape(tmp_path):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["steps"]["first"]["skills"] = ["../secret"]
    with pytest.raises(ConfigurationError, match="escapes"):
        snapshot(write(tmp_path, data))


def test_result_schema_rejects_remote_references(tmp_path):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["steps"]["first"]["result_schema"] = {
        "type": "object",
        "properties": {"value": {"$ref": "https://attacker.invalid/schema.json"}},
    }
    with pytest.raises(ConfigurationError, match="local fragments"):
        snapshot(write(tmp_path, data))


def test_snapshot_rejects_external_aorch_symlink(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "config.yaml").write_text(yaml.safe_dump(BASE))
    (project / ".aorch").symlink_to(external, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="symbolic link"):
        snapshot(project)


def test_snapshot_changes_with_prompt(tmp_path):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["steps"]["first"]["skills"] = ["instruction.md"]
    prompt = tmp_path / "instruction.md"
    prompt.write_text("original")
    source = write(tmp_path, data)
    original = snapshot(source)
    prompt.write_text("updated")
    assert original["sha256"] != snapshot(source)["sha256"]
    assert original["files"]["instruction.md"]["content"] == "original"


def test_agent_accepts_inline_prompt_text(tmp_path):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "reviewer": {"adapter": "codex", "authentication": "local"}
    }
    data["workflows"]["work"]["steps"]["review"] = {
        "type": "agent",
        "profile": "reviewer",
        "prompt_text": "Review the changes.\nReport concrete bugs.",
    }
    package = snapshot(write(tmp_path, data))
    step = package["configuration"]["workflows"]["work"]["steps"]["review"]
    assert step["prompt_text"] == "Review the changes.\nReport concrete bugs."


@pytest.mark.parametrize("root", ["relative/cache", "/"])
def test_agent_rejects_unsafe_additional_writable_roots(tmp_path, root):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "writer": {
            "adapter": "codex",
            "authentication": "local",
            "additional_writable_roots": [root],
        }
    }
    data["workflows"]["work"]["steps"]["write"] = {
        "type": "agent",
        "profile": "writer",
        "prompt_text": "Write a file.",
    }
    with pytest.raises(ConfigurationError, match="Additional writable roots"):
        snapshot(write(tmp_path, data))


def test_agent_limits_additional_writable_roots_to_codex_workspace_write(tmp_path):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "writer": {
            "adapter": "claude",
            "authentication": "local",
            "additional_writable_roots": [str(tmp_path / "extra")],
        }
    }
    data["workflows"]["work"]["steps"]["write"] = {
        "type": "agent",
        "profile": "writer",
        "prompt_text": "Write a file.",
    }
    with pytest.raises(ConfigurationError, match="supported only by the Codex"):
        snapshot(write(tmp_path, data))


def test_agent_accepts_narrow_absolute_codex_writable_root(tmp_path):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "writer": {
            "adapter": "codex",
            "authentication": "local",
            "additional_writable_roots": [str(tmp_path / "tool-cache")],
        }
    }
    data["workflows"]["work"]["steps"]["write"] = {
        "type": "agent",
        "profile": "writer",
        "prompt_text": "Write a file.",
    }
    profile = snapshot(write(tmp_path, data))["configuration"]["profiles"]["writer"]
    assert profile["additional_writable_roots"] == [str(tmp_path / "tool-cache")]


@pytest.mark.parametrize(
    "prompt_fields",
    [{}, {"prompt": "review.md", "prompt_text": "Review."}, {"prompt_text": "  "}],
)
def test_agent_rejects_invalid_prompt_fields(tmp_path, prompt_fields):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "reviewer": {"adapter": "codex", "authentication": "local"}
    }
    data["workflows"]["work"]["steps"]["review"] = {
        "type": "agent",
        "profile": "reviewer",
        **prompt_fields,
    }
    with pytest.raises(ConfigurationError, match="config.yaml"):
        snapshot(write(tmp_path, data))


def test_snapshot_changes_with_inline_prompt_text(tmp_path):
    data = copy.deepcopy(BASE)
    data["profiles"] = {
        "reviewer": {"adapter": "codex", "authentication": "local"}
    }
    data["workflows"]["work"]["steps"]["review"] = {
        "type": "agent",
        "profile": "reviewer",
        "prompt_text": "Review the changes.",
    }
    source = write(tmp_path, data)
    original = snapshot(source)
    data["workflows"]["work"]["steps"]["review"]["prompt_text"] = (
        "Review the changes. Report bugs."
    )
    write(tmp_path, data)
    assert original["sha256"] != snapshot(source)["sha256"]


def test_conditions():
    context = {"steps": {"first": {"status": "succeeded"}}, "inputs": {"enabled": True}}
    assert evaluate_condition(
        "steps.first.status == 'succeeded' and inputs.enabled", context
    )
    assert not evaluate_condition("False and inputs.missing", context)


def test_schema():
    result = CliRunner().invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "configuration" in json.loads(result.stdout)


def test_runner_enroll_validates_remote_url_before_request(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "runner-enroll",
            "--engine-url",
            "http://169.254.169.254",
            "--setup-key",
            "k" * 40,
            "--id",
            "runner",
            "--directory",
            str(tmp_path / "runners"),
        ],
    )
    assert result.exit_code != 0
    assert "Invalid engine URL" in result.output
    assert not (tmp_path / "runners").exists()


def test_version_two_periodic_trigger(tmp_path):
    data = copy.deepcopy(BASE)
    data["version"] = 2
    data["workflows"]["work"]["triggers"] = [
        {"type": "periodic", "interval_seconds": 60, "enabled": True}
    ]
    package = snapshot(write(tmp_path, data))
    assert package["configuration"]["workflows"]["work"]["triggers"][0]["interval_seconds"] == 60


def test_reject_invalid_periodic_trigger(tmp_path):
    data = copy.deepcopy(BASE)
    data["version"] = 2
    data["workflows"]["work"]["triggers"] = [
        {"type": "periodic", "interval_seconds": 0}
    ]
    with pytest.raises(ConfigurationError):
        snapshot(write(tmp_path, data))


def test_explicit_trigger_requires_version_two(tmp_path):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["triggers"] = [
        {"type": "periodic", "interval_seconds": 60}
    ]
    with pytest.raises(ConfigurationError, match="version 2"):
        snapshot(write(tmp_path, data))


@pytest.mark.parametrize(
    "condition", ["steps.", "inputs.__class__", "inputs.x[0]", "sum(inputs)"]
)
def test_invalid_conditions(tmp_path, condition):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["steps"]["second"]["when"] = condition
    with pytest.raises(ConfigurationError):
        snapshot(write(tmp_path, data))


def test_invalid_result_schema(tmp_path):
    data = copy.deepcopy(BASE)
    data["workflows"]["work"]["steps"]["first"]["result_schema"] = {"type": "invalid"}
    with pytest.raises(ConfigurationError, match="result schema"):
        snapshot(write(tmp_path, data))


def modular_project(tmp_path):
    root = tmp_path / ".aorch"
    for folder in ("profiles", "actions", "workflows", "prompts", "scripts"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "version: 3\nname: modular\nimports:\n"
        "  - profiles/writers.yaml\n  - profiles/reviewers.yaml\n"
        "  - actions/first.yaml\n  - actions/second.yaml\n"
        "  - workflows/first.yaml\n  - workflows/second.yaml\n"
    )
    profile = "adapter: claude\nauthentication: local\nsandbox: read-only\ntools: [read]\n"
    (root / "profiles/writers.yaml").write_text("profiles:\n  writer:\n" + "".join(f"    {line}\n" for line in profile.splitlines()))
    (root / "profiles/reviewers.yaml").write_text("profiles:\n  reviewer:\n" + "".join(f"    {line}\n" for line in profile.splitlines()))
    action = (
        "argv: [python3, '@package/scripts/first.py']\n"
        "files: [scripts/first.py]\n"
        "input_schema: {type: object, properties: {}, additionalProperties: false}\n"
        "result_schema: {type: object, properties: {}, additionalProperties: true}\n"
    )
    (root / "actions/first.yaml").write_text("actions:\n  first:\n" + "".join(f"    {line}\n" for line in action.splitlines()))
    action = action.replace("first.py", "second.py").replace("first:", "second:")
    (root / "actions/second.yaml").write_text("actions:\n  second:\n" + "".join(f"    {line}\n" for line in action.splitlines()))
    (root / "scripts/first.py").write_text("print('first')\n")
    (root / "scripts/second.py").write_text("print('second')\n")
    (root / "workflows/first.yaml").write_text(
        "workflows:\n  first:\n    steps:\n      run:\n        type: action\n        action: first\n"
    )
    (root / "workflows/second.yaml").write_text(
        "workflows:\n  second:\n    steps:\n      run:\n        type: action\n        action: second\n"
    )
    return root, root / "config.yaml"


def test_modular_imports_merge_all_named_sections_and_snapshot_sources(tmp_path):
    root, source = modular_project(tmp_path)
    first = snapshot(source)
    assert set(first["configuration"]["profiles"]) == {"writer", "reviewer"}
    assert set(first["configuration"]["actions"]) == {"first", "second"}
    assert set(first["configuration"]["workflows"]) == {"first", "second"}
    assert "actions/first.yaml" in first["config_sources"]
    assert "scripts/second.py" in first["files"]
    (root / "actions/second.yaml").write_text(
        (root / "actions/second.yaml").read_text() + "# updated\n"
    )
    assert snapshot(source)["sha256"] != first["sha256"]


def test_modular_snapshot_is_self_contained_after_source_removal(tmp_path):
    from aorch.runner.package import verify_package

    root, source = modular_project(tmp_path)
    package = snapshot(source)
    import shutil

    shutil.rmtree(root)
    verify_package(package)


def test_modular_duplicate_names_report_both_source_files(tmp_path):
    root, source = modular_project(tmp_path)
    (root / "profiles/reviewers.yaml").write_text(
        (root / "profiles/reviewers.yaml").read_text().replace("reviewer:", "writer:")
    )
    with pytest.raises(ConfigurationError) as error:
        snapshot(source)
    assert "profiles/writers.yaml" in str(error.value)
    assert "profiles/reviewers.yaml" in str(error.value)


def test_modular_duplicate_between_main_and_fragment_reports_both_files(tmp_path):
    _, source = modular_project(tmp_path)
    with source.open("a") as stream:
        stream.write("profiles:\n  writer:\n    adapter: claude\n")
    with pytest.raises(ConfigurationError) as error:
        snapshot(source)
    assert "config.yaml:" in str(error.value)
    assert "profiles/writers.yaml:" in str(error.value)


def test_nested_diamond_import_graph_loads_shared_fragment_once(tmp_path):
    root, source = modular_project(tmp_path)
    (root / "actions/shared.yaml").write_text("actions: {}\n")
    for name in ("first", "second"):
        fragment = root / f"actions/{name}.yaml"
        fragment.write_text("imports: [shared.yaml]\n" + fragment.read_text())
    (root / "actions/nested.yaml").write_text(
        "imports: [first.yaml, second.yaml]\n"
    )
    source.write_text(
        source.read_text().replace("  - actions/first.yaml\n  - actions/second.yaml\n", "  - actions/nested.yaml\n")
    )
    package = snapshot(source)
    assert "actions/shared.yaml" in package["config_sources"]
    assert set(package["configuration"]["actions"]) == {"first", "second"}


@pytest.mark.parametrize(
    "reference", ["../outside.yaml", "/tmp/outside.yaml", "missing.yaml", "fragment.txt"]
)
def test_modular_import_rejects_unsafe_or_invalid_paths(tmp_path, reference):
    _, source = modular_project(tmp_path)
    source.write_text(f"version: 3\nname: modular\nimports: [{reference!r}]\n")
    with pytest.raises(ConfigurationError):
        snapshot(source)


def test_modular_import_rejects_symlinks_outside_root(tmp_path):
    root, source = modular_project(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("profiles: {}\n")
    (root / "escape.yaml").symlink_to(outside)
    source.write_text("version: 3\nname: modular\nimports: [escape.yaml]\n")
    with pytest.raises(ConfigurationError, match="escapes configuration root"):
        snapshot(source)


def test_modular_import_limits_are_enforced(tmp_path, monkeypatch):
    from aorch.config import loader

    root, source = modular_project(tmp_path)
    (root / "one.yaml").write_text("profiles: {}\n")
    (root / "two.yaml").write_text("profiles: {}\n")
    source.write_text("version: 3\nname: modular\nimports: [one.yaml, two.yaml]\n")
    monkeypatch.setattr(loader, "MAX_FILES", 2)
    with pytest.raises(ConfigurationError, match="import count exceeds"):
        snapshot(source)

    monkeypatch.setattr(loader, "MAX_FILES", 128)
    (root / "one.yaml").write_text("imports: [two.yaml]\n")
    (root / "two.yaml").write_text("profiles: {}\n")
    source.write_text("version: 3\nname: modular\nimports: [one.yaml]\n")
    monkeypatch.setattr(loader, "MAX_DEPTH", 1)
    with pytest.raises(ConfigurationError, match="import depth exceeds"):
        snapshot(source)

    monkeypatch.setattr(loader, "MAX_DEPTH", 16)
    monkeypatch.setattr(loader, "MAX_BYTES", 8)
    with pytest.raises(ConfigurationError, match="exceeds 8 bytes"):
        snapshot(source)


def test_modular_import_cycle_reports_chain(tmp_path):
    root, source = modular_project(tmp_path)
    (root / "workflows/first.yaml").write_text("imports: [second.yaml]\n")
    (root / "workflows/second.yaml").write_text("imports: [first.yaml]\n")
    with pytest.raises(ConfigurationError, match="Import cycle"):
        snapshot(source)


def test_actions_cli_describes_shared_contract_catalog():
    listed = CliRunner().invoke(app, ["actions", "list", "--json"])
    assert listed.exit_code == 0
    catalog = json.loads(listed.stdout)
    assert catalog["github.open_pr"]["contract_version"] == 2
    assert catalog["github.open_pr"]["external_effect"] is True
    described = CliRunner().invoke(app, ["actions", "describe", "git.commit", "--json"])
    assert described.exit_code == 0
    assert json.loads(described.stdout)["outputs_schema"]["required"] == ["commit", "branch"]
