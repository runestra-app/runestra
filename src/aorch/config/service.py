"""Strict engine and runner configuration without embedded secrets."""

import re
from ipaddress import ip_address
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from aorch.config import UniqueLoader
from aorch.contracts import Contract


class GitProjectSource(Contract):
    git: str = Field(min_length=1)
    ref: str = "HEAD"

    @model_validator(mode="after")
    def validate_remote(self):
        from urllib.parse import urlsplit

        if self.git.startswith("git@"):
            if ":" not in self.git or self.git.startswith("git@:"):
                raise ValueError("Invalid SSH Git URL")
        else:
            parsed = urlsplit(self.git)
            if parsed.scheme not in ("https", "ssh") or not parsed.hostname:
                raise ValueError("Git source must be an SSH or HTTPS URL")
            if parsed.password or (parsed.username and (parsed.scheme != "ssh" or parsed.username != "git")):
                raise ValueError("Git URLs cannot contain embedded credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("Git URLs cannot contain queries or fragments")
            try:
                port = parsed.port
            except ValueError as error:
                raise ValueError("Invalid Git port") from error
            if parsed.scheme == "https" and port not in (None, 443):
                raise ValueError("HTTPS Git sources must use port 443")
        if self.ref.startswith("-") or ".." in self.ref.split("/"):
            raise ValueError("Invalid Git ref")
        return self


class EngineConfig(Contract):
    database: Path = Path("engine.sqlite")
    artifacts: Path = Path("artifacts")
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    admin_token_file: Path | None = None
    session_key_file: Path
    secure_cookies: bool = False
    public_base_url: str = "http://127.0.0.1:8765"
    git_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["github.com", "gitlab.com", "bitbucket.org"]
    )
    registration_enabled: bool = True
    local_auth: bool = False
    loopback_port_forward: bool = False
    session_ttl_seconds: int = Field(default=28800, ge=300, le=2592000)
    magic_link_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    invitation_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    secrets_file: Path | None = None
    brevo_api_key: str = ""
    email_from: str = "no-reply@runestra.app"
    google_client_id: str = "DUMMY_GOOGLE_CLIENT_ID"
    google_client_secret: str = "DUMMY_GOOGLE_CLIENT_SECRET"
    github_client_id: str = "DUMMY_GITHUB_CLIENT_ID"
    github_client_secret: str = "DUMMY_GITHUB_CLIENT_SECRET"
    webhook_triggers: dict[str, dict[str, object]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_secure_remote_sessions(self):
        if not self.git_allowed_hosts or any(
            not isinstance(host, str)
            or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
                host,
            )
            for host in self.git_allowed_hosts
        ):
            raise ValueError("git_allowed_hosts must contain lowercase host names")
        self.git_allowed_hosts = sorted(set(self.git_allowed_hosts))
        try:
            loopback = ip_address(self.host).is_loopback
        except ValueError:
            loopback = self.host == "localhost"
        if not loopback and not self.secure_cookies and not self.loopback_port_forward:
            raise ValueError(
                "Non-loopback engine binds require secure_cookies behind HTTPS"
            )
        from urllib.parse import urlsplit

        public_url = urlsplit(self.public_base_url)
        if public_url.scheme not in ("http", "https") or not public_url.hostname:
            raise ValueError("public_base_url must be an absolute HTTP(S) URL")
        if public_url.scheme != "https" and public_url.hostname not in (
            "localhost", "127.0.0.1", "::1"
        ):
            raise ValueError("Remote public_base_url requires HTTPS")
        if public_url.scheme == "https" and not self.secure_cookies:
            raise ValueError("HTTPS public_base_url requires secure_cookies")
        for hook_id, hook in self.webhook_triggers.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", hook_id) or not isinstance(hook, dict):
                raise ValueError("Webhook trigger IDs and definitions are invalid")
            if hook.get("provider") not in {"github", "hmac"} or not isinstance(hook.get("secret"), str) or len(hook["secret"]) < 32:
                raise ValueError("Webhook triggers require a provider and a secret of at least 32 characters")
            if not all(isinstance(hook.get(key), str) and hook[key] for key in ("project", "workflow")):
                raise ValueError("Webhook triggers require project and workflow targets")
            mapping = hook.get("input_mapping")
            if not isinstance(mapping, dict) or not mapping or any(
                not isinstance(target, str) or not target.isidentifier()
                or not isinstance(reference, str) or any(not part or part.startswith("_") for part in reference.split("."))
                for target, reference in mapping.items()
            ):
                raise ValueError("Webhook triggers require an explicit safe input_mapping")
            if hook["provider"] == "github":
                if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(hook.get("repository", ""))):
                    raise ValueError("GitHub webhook triggers require an owner/repository allowlist")
                if not isinstance(hook.get("event", "issues"), str) or not isinstance(hook.get("actions", ["opened"]), list):
                    raise ValueError("GitHub webhook event filters are invalid")
        if self.local_auth and (
            public_url.hostname not in ("localhost", "127.0.0.1", "::1")
            or public_url.scheme != "http"
        ):
            raise ValueError("local_auth is only available for a loopback HTTP URL")
        try:
            bind_loopback = ip_address(self.host).is_loopback
        except ValueError:
            bind_loopback = self.host == "localhost"
        if self.local_auth and not bind_loopback and not self.loopback_port_forward:
            raise ValueError("local_auth requires a loopback bind or an explicitly loopback-published Compose port")
        return self


class RunnerConfig(Contract):
    id: str
    engine_url: str = "http://127.0.0.1:8765"
    token_file: Path
    workspace_root: Path = Path("workspaces")
    journal: Path = Path("runner.sqlite")
    log_directory: Path = Path("logs")
    log_retention_days: int = Field(default=7, ge=1, le=365)
    log_max_total_bytes: int = Field(default=2 * 1024**3, ge=1024)
    log_max_attempt_bytes: int = Field(default=50 * 1024**2, ge=1024)
    capacity: int = Field(default=1, ge=1)
    adapters: list[str] = Field(default_factory=lambda: ["action", "shell"])
    environment: list[str] = Field(default_factory=list)
    connections: dict[str, dict[str, str]] = Field(default_factory=dict)
    repositories: dict[str, Path] = Field(default_factory=dict)
    allow_file_git_sources: bool = False
    devices: dict[str, dict[str, str]] = Field(default_factory=dict)
    api_credentials: dict[str, str] = Field(default_factory=dict)
    redact_env: list[str] = Field(default_factory=list)
    command_allowlist: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_url(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.engine_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Runner engine URLs cannot contain credentials, queries, or fragments")
        if any(
            not key.replace("_", "").isalnum() or not value.replace("_", "").isalnum()
            for key, value in self.api_credentials.items()
        ):
            raise ValueError("API credential mapping must name environment variables")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError("Remote engines require HTTPS")
        return self


def load_service(path: Path, model):
    data = yaml.load(path.read_text(), Loader=UniqueLoader)
    if model is EngineConfig and any(
        key in data
        for key in ("brevo_api_key", "google_client_secret", "github_client_secret", "webhook_triggers")
    ):
        raise ValueError("Provider secrets must be stored in secrets_file, not engine.yaml")
    if model is EngineConfig and data.get("secrets_file"):
        secrets_path = Path(data["secrets_file"])
        if not secrets_path.is_absolute():
            secrets_path = (path.resolve().parent / secrets_path).resolve()
        if secrets_path.stat().st_mode & 0o077:
            raise ValueError(f"Secret file must have mode 0600: {secrets_path}")
        secret_data = yaml.load(secrets_path.read_text(), Loader=UniqueLoader)
        allowed = {
            "brevo_api_key", "google_client_secret", "github_client_secret",
            "webhook_triggers",
        }
        if not isinstance(secret_data, dict) or set(secret_data) - allowed:
            raise ValueError("Secret file contains unknown fields")
        data.update(secret_data)
        data["secrets_file"] = secrets_path
    config = model.model_validate(data)
    for field in model.model_fields:
        value = getattr(config, field)
        if isinstance(value, Path) and not value.is_absolute():
            setattr(config, field, (path.resolve().parent / value).resolve())
    if isinstance(config, RunnerConfig):
        if not config.log_directory.is_absolute():
            config.log_directory = (path.resolve().parent / config.log_directory).resolve()
        config.repositories = {
            key: (path.resolve().parent / value).resolve()
            for key, value in config.repositories.items()
        }
    return config


def read_secret(path: Path):
    if path.stat().st_mode & 0o077:
        raise ValueError(f"Secret file must have mode 0600: {path}")
    value = path.read_text().strip()
    if len(value) < 32:
        raise ValueError("Secret must contain at least 32 characters")
    return value
