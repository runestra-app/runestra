from pathlib import Path

import pytest

from aorch.config.service import RunnerConfig


def test_runner_agent_execution_defaults_sandboxed_and_accepts_trusted_host():
    assert RunnerConfig(id="test", token_file=Path("runner.token")).agent_execution == "sandboxed"
    assert RunnerConfig(
        id="trusted", token_file=Path("runner.token"), agent_execution="trusted-host"
    ).agent_execution == "trusted-host"
    with pytest.raises(ValueError):
        RunnerConfig(
            id="invalid", token_file=Path("runner.token"), agent_execution="unrestricted"
        )
