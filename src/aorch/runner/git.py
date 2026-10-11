"""Temporary Git authentication and isolated Git subprocess settings."""

import os
import shlex
from pathlib import Path
from urllib.parse import urlsplit


def git_environment(base, remote, temporary, allow_file=False, project_names=()):
    project_names = set(project_names)
    git_credentials = {
        "RUNESTRA_GIT_SSH_PRIVATE_KEY",
        "RUNESTRA_GIT_KNOWN_HOSTS",
        "RUNESTRA_GIT_USERNAME",
        "RUNESTRA_GIT_TOKEN",
    }
    environment = {
        key: value
        for key, value in base.items()
        if not key.startswith("GIT_")
        and (key not in project_names or key in git_credentials)
    }
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https:ssh:file" if allow_file else "https:ssh",
    })
    parsed = urlsplit(remote if not remote.startswith("git@") else f"ssh://{remote[4:].replace(':', '/', 1)}")
    if parsed.scheme == "ssh":
        options = ["-o StrictHostKeyChecking=yes"]
        private_key = base.get("RUNESTRA_GIT_SSH_PRIVATE_KEY")
        if private_key:
            key_path = Path(temporary) / "ssh-key"
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as key_file:
                key_file.write(private_key)
            options.extend(["-i", shlex.quote(str(key_path)), "-o IdentitiesOnly=yes"])
        environment.pop("RUNESTRA_GIT_SSH_PRIVATE_KEY", None)
        known_hosts = base.get("RUNESTRA_GIT_KNOWN_HOSTS")
        if known_hosts:
            known_hosts_path = Path(temporary) / "known_hosts"
            descriptor = os.open(known_hosts_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as hosts_file:
                hosts_file.write(known_hosts)
            options.extend([
                "-o UserKnownHostsFile=" + shlex.quote(str(known_hosts_path)),
            ])
        environment.pop("RUNESTRA_GIT_KNOWN_HOSTS", None)
        environment.pop("RUNESTRA_GIT_USERNAME", None)
        environment.pop("RUNESTRA_GIT_TOKEN", None)
        if options:
            environment["GIT_SSH_COMMAND"] = "ssh " + " ".join(options)
    elif parsed.scheme == "https":
        environment.pop("RUNESTRA_GIT_SSH_PRIVATE_KEY", None)
        environment.pop("RUNESTRA_GIT_KNOWN_HOSTS", None)
        username, access_token = base.get("RUNESTRA_GIT_USERNAME"), base.get("RUNESTRA_GIT_TOKEN")
        if username and access_token:
            askpass = Path(temporary) / "git-askpass"
            askpass.write_text(
                "#!/bin/sh\ncase \"$1\" in *sername*) printf '%s\\n' \"$RUNESTRA_GIT_USERNAME\";; *) printf '%s\\n' \"$RUNESTRA_GIT_TOKEN\";; esac\n"
            )
            askpass.chmod(0o700)
            environment["GIT_ASKPASS"] = str(askpass)
    elif not allow_file:
        raise ValueError("Local file Git sources are disabled")
    else:
        for name in git_credentials:
            environment.pop(name, None)
    return environment
