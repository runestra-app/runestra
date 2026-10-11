#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/runestra-app/runestra.git"
install_dir="${RUNESTRA_INSTALL_DIR:-$HOME/.local/share/runestra}"
state_dir="${RUNESTRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/runestra}"
version="${RUNESTRA_VERSION:-latest}"

if ! command -v curl >/dev/null 2>&1; then
  printf 'Runestra setup requires curl to download the installer.\n' >&2
  exit 1
fi

if [ "$version" = latest ]; then
  release_response=$(curl -sSL --max-time 15 -w '\n%{http_code}' https://api.github.com/repos/runestra-app/runestra/releases/latest) || {
    printf 'Could not query GitHub for the latest Runestra release. Check the network or set RUNESTRA_VERSION to a release tag.\n' >&2
    exit 1
  }
  release_status=${release_response##*$'\n'}
  release_payload=${release_response%$'\n'*}
  if [ "$release_status" = 404 ]; then
    printf 'No published Runestra release was found. Create a tagged GitHub release or set RUNESTRA_VERSION to an existing release tag.\n' >&2
    exit 1
  elif [ "$release_status" != 200 ]; then
    printf 'GitHub returned HTTP %s while checking for a Runestra release. Retry or set RUNESTRA_VERSION to a release tag.\n' "$release_status" >&2
    exit 1
  fi
  version=$(printf '%s\n' "$release_payload" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
  if [ -z "$version" ]; then
    printf 'No published Runestra release was found. Set RUNESTRA_VERSION to a release tag and rerun.\n' >&2
    exit 1
  fi
fi
if ! command -v git >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        brew install git
      else
        xcode-select --install || true
        printf 'Complete the Git command line tools installation, then rerun.\n' >&2
        exit 1
      fi
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then apt-get update && apt-get install -y git
        elif command -v sudo >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y git
        else printf 'Install git, then rerun this command.\n' >&2; exit 1
        fi
      elif command -v dnf >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then dnf install -y git
        elif command -v sudo >/dev/null 2>&1; then sudo dnf install -y git
        else printf 'Install git, then rerun this command.\n' >&2; exit 1
        fi
      else
        printf 'Install git with your Linux package manager, then rerun.\n' >&2
        exit 1
      fi
      ;;
    *) printf 'Install git, then rerun this command.\n' >&2; exit 1 ;;
  esac
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

docker_command=(docker)
if ! command -v docker >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        printf 'Install Homebrew and rerun this command to install Docker Desktop automatically.\n' >&2
        exit 1
      fi
      brew install --cask docker
      open -a Docker
      ;;
    Linux)
      if [ "$(id -u)" -eq 0 ]; then
        curl -fsSL https://get.docker.com | sh
      elif command -v sudo >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sudo sh
        docker_command=(sudo docker)
      else
        printf 'Docker is missing and sudo is unavailable. Install Docker Engine and Compose v2, then rerun.\n' >&2
        exit 1
      fi
      ;;
    *)
      printf 'Install Docker Desktop on Windows, enable WSL2 integration, and rerun this command inside WSL2.\n' >&2
      exit 1
      ;;
  esac
fi

if ! "${docker_command[@]}" info >/dev/null 2>&1; then
  if [ "$(uname -s)" = Darwin ]; then
    open -a Docker
    printf 'Waiting for Docker Desktop. Complete its first-run prompts if they appear.'
    for attempt in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then
        printf '\n'
        break
      fi
      printf '.'
      sleep 2
    done
  elif [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    docker_command=(sudo docker)
    sudo systemctl start docker 2>/dev/null || sudo service docker start
  elif command -v systemctl >/dev/null 2>&1; then
    systemctl start docker 2>/dev/null || service docker start
  fi
fi
if ! "${docker_command[@]}" info >/dev/null 2>&1; then
  printf 'Docker is installed but not running. Start it, accept its first-run prompts if shown, then rerun.\n' >&2
  exit 1
fi
if ! "${docker_command[@]}" compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 is required. Update Docker Desktop or install the Compose plugin, then rerun.\n' >&2
  exit 1
fi

if [ ! -d "$install_dir/.git" ]; then
  mkdir -p "$(dirname "$install_dir")"
  git clone --branch "$version" --depth 1 "$repo_url" "$install_dir"
elif [ -n "$(git -C "$install_dir" status --porcelain)" ]; then
  printf 'The Runestra checkout has local changes. Save them elsewhere before updating; user data in .runtime is kept separately.\n' >&2
  exit 1
else
  git -C "$install_dir" fetch --depth 1 origin "refs/tags/$version:refs/tags/$version"
  git -C "$install_dir" checkout --detach "$version"
fi

cd "$install_dir"
uv sync --locked
export RUNESTRA_UID="$(id -u)"
export RUNESTRA_GID="$(id -g)"
if [ ! -d .runtime ] && [ -d "$state_dir/runtime" ]; then
  mv "$state_dir/runtime" .runtime
fi
mkdir -p .runtime
if [ ! -f .runtime/local/engine.yaml ]; then
  uv run aorch init-local examples/local-actions --directory .runtime/local --project-mount-path /workspace/examples/local-actions
fi

mode_file=.runtime/local/install-mode
if [ ! -f "$mode_file" ]; then
  printf 'Local sign-in skips email verification and is available only through the localhost port.\n'
  printf 'Choose sign-in mode, local or email [local]: '
  IFS= read -r mode
  mode=${mode:-local}
  case "$mode" in
    local|email) printf '%s\n' "$mode" > "$mode_file" ;;
    *) printf 'Choose local or email.\n' >&2; exit 1 ;;
  esac
  chmod 600 "$mode_file"
fi
mode=$(cat "$mode_file")

if [ "$mode" = email ] && grep -q 'DUMMY_BREVO_API_KEY' .runtime/local/auth-secrets.yaml; then
  printf 'Brevo API key: '
  IFS= read -r -s brevo_key
  printf '\n'
  if [ -z "$brevo_key" ]; then
    printf 'Brevo API key cannot be empty.\n' >&2
    exit 1
  fi
  RUNESTRA_BREVO_KEY="$brevo_key" uv run python - <<'PY'
import os
from pathlib import Path
path = Path(".runtime/local/auth-secrets.yaml")
text = path.read_text().replace("DUMMY_BREVO_API_KEY", os.environ["RUNESTRA_BREVO_KEY"])
path.write_text(text)
path.chmod(0o600)
PY
  unset brevo_key
fi

if [ "$mode" = email ] && [ ! -f .runtime/local/.owner-ready ]; then
  printf 'Owner email: '
  IFS= read -r owner_email
  printf 'Owner username [owner]: '
  IFS= read -r owner_username
  owner_username=${owner_username:-owner}
  printf 'Organization [local]: '
  IFS= read -r owner_org
  owner_org=${owner_org:-local}
  uv run aorch bootstrap-owner --email "$owner_email" --username "$owner_username" --organization "$owner_org" --config .runtime/local/engine.yaml
  touch .runtime/local/.owner-ready
fi

compose=("${docker_command[@]}" compose -f compose.yaml)
if [ "$mode" = local ]; then
  compose+=(-f compose.local-auth.yaml)
fi
"${compose[@]}" up -d --build
printf 'Waiting for the engine'
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8765/health >/dev/null; then
    printf '\nRunestra is ready at http://127.0.0.1:8765\n'
    if [ "$mode" = local ]; then
      printf 'Open the page and choose Continue locally.\n'
    else
      printf 'Open the page and request an email sign-in link.\n'
    fi
    printf 'Runner is running in this terminal. Press Ctrl+C to stop the runner.\n'
    exec uv run aorch runner .runtime/local/runner.yaml
  fi
  printf '.'
  sleep 2
done
printf '\nEngine did not become ready. Inspect it with: %s compose -f compose.yaml logs engine\n' "${docker_command[*]}" >&2
exit 1
