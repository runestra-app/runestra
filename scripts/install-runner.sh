#!/usr/bin/env bash
set -euo pipefail

engine_url=
setup_key=
runner_id=
version=latest
while [ "$#" -gt 0 ]; do
  case "$1" in
    --engine-url) engine_url="$2"; shift 2 ;;
    --setup-key) setup_key="$2"; shift 2 ;;
    --id) runner_id="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [ -z "$engine_url" ] || [ -z "$setup_key" ] || [ -z "$runner_id" ]; then
  printf 'Usage: install-runner.sh --engine-url URL --setup-key KEY --id ID\n' >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  printf 'Install curl, then rerun this command.\n' >&2
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
    printf 'No published Runestra release was found. Pass --version with a release tag and rerun.\n' >&2
    exit 1
  fi
fi
if ! command -v git >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then brew install git
      else xcode-select --install || true; printf 'Complete the Git command line tools installation, then rerun.\n' >&2; exit 1
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
      else printf 'Install git with your Linux package manager, then rerun.\n' >&2; exit 1
      fi
      ;;
    *) printf 'Install git, then rerun this command.\n' >&2; exit 1 ;;
  esac
fi
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

repo="git+https://github.com/runestra-app/runestra.git@$version"
uvx --refresh --from "$repo" aorch runner-enroll --engine-url "$engine_url" --setup-key "$setup_key" --id "$runner_id"
exec uvx --refresh --from "$repo" aorch runner "$HOME/.config/runestra/runners/$runner_id/runner.yaml"
