import shutil

import typer
from typer.testing import CliRunner

from aorch.cli import app


def test_agent_test_rejects_unknown_adapter_before_any_provider_call():
    result = CliRunner().invoke(app, ["agent-test", "openai"])
    assert result.exit_code == 2
    assert "Choose codex or claude" in result.output


def test_agent_test_decline_never_starts_provider_process(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(typer, "confirm", lambda *args, **kwargs: False)

    def unexpected_process(*args, **kwargs):
        raise AssertionError("Provider process must not run without confirmation")

    monkeypatch.setattr("aorch.runner.process.run_process", unexpected_process)
    result = CliRunner().invoke(app, ["agent-test", "claude"])
    assert result.exit_code == 0
    assert "Cancelled" in result.output


def test_agent_test_reports_missing_cli_without_model_request(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = CliRunner().invoke(app, ["agent-test", "codex"])
    assert result.exit_code == 1
    assert "Missing codex CLI" in result.output


def test_doctor_rejects_credentials_in_engine_url_without_printing_them(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("Doctor must reject credential-bearing URLs before connecting")

    monkeypatch.setattr("httpx.get", unexpected_request)
    result = CliRunner().invoke(
        app,
        ["doctor", "--engine-url", "https://user:secret-value@example.test"],
    )
    assert result.exit_code == 1
    assert "Engine URL must be HTTPS" in result.output
    assert "secret-value" not in result.output
