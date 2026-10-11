#!/usr/bin/env bash
set -euo pipefail

install_dir="${RUNESTRA_INSTALL_DIR:-$HOME/.local/share/runestra}"
state_dir="${RUNESTRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/runestra}"
purge_data=false
if [ "${1:-}" = "--purge-data" ] && [ "$#" -eq 1 ]; then
  purge_data=true
elif [ "$#" -gt 0 ]; then
  printf 'Usage: uninstall-local.sh [--purge-data]\n' >&2
  exit 2
fi

if [ ! -d "$install_dir" ]; then
  printf 'Runestra installation not found at %s\n' "$install_dir" >&2
  exit 1
fi
if [ -L "$install_dir" ]; then
  printf 'Refusing to remove an unsafe installation path: %s\n' "$install_dir" >&2
  exit 1
fi
install_dir=$(cd "$install_dir" && pwd -P)
if [ "$install_dir" = / ] || [ "$install_dir" = "$HOME" ]; then
  printf 'Refusing to remove an unsafe installation path: %s\n' "$install_dir" >&2
  exit 1
fi
if [ ! -e "$install_dir/.git" ]; then
  printf 'The target is not a managed Runestra Git installation: %s\n' "$install_dir" >&2
  exit 1
fi
if [ -n "$(git -C "$install_dir" status --porcelain --untracked-files=all)" ]; then
  printf 'The installation contains local code changes. Save them before uninstalling.\n' >&2
  exit 1
fi
printf 'Stop the foreground runner with Ctrl+C before continuing.\n'
if [ -d "$install_dir/.runtime" ]; then
  if [ "$purge_data" = true ]; then
    printf 'This permanently deletes the Runestra database, artifacts, runner configuration, and workspaces.\n'
    printf 'Type DELETE to confirm: '
    IFS= read -r confirmation
    if [ "$confirmation" != DELETE ]; then
      printf 'Cancelled.\n'
      exit 1
    fi
  elif [ -e "$state_dir/runtime" ]; then
    printf 'Preserved data already exists at %s/runtime; refusing to overwrite it.\n' "$state_dir" >&2
    exit 1
  fi
fi

if command -v docker >/dev/null 2>&1 && [ -f "$install_dir/compose.yaml" ]; then
  compose=(docker compose -f "$install_dir/compose.yaml")
  if [ -f "$install_dir/compose.local-auth.yaml" ] && [ -f "$install_dir/.runtime/local/install-mode" ] && [ "$(cat "$install_dir/.runtime/local/install-mode")" = local ]; then
    compose+=(-f "$install_dir/compose.local-auth.yaml")
  fi
  "${compose[@]}" down
fi

if [ -d "$install_dir/.runtime" ] && [ "$purge_data" = false ]; then
  mkdir -p "$state_dir"
  chmod 700 "$state_dir"
  mv "$install_dir/.runtime" "$state_dir/runtime"
  printf 'Runestra data preserved at %s/runtime\n' "$state_dir"
fi
rm -rf "$install_dir"
printf 'Runestra installation removed from %s\n' "$install_dir"
