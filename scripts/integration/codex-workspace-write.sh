#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "SKIP: Codex workspace-write integration requires macOS sandboxing."
  exit 0
fi
command -v codex >/dev/null || { echo "codex CLI is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v clang >/dev/null || { echo "clang is required" >&2; exit 1; }

root="$(mktemp -d "${TMPDIR:-/tmp}/runestra-codex-sandbox.XXXXXX")"
root="$(cd "$root" && pwd -P)"
trap 'rm -rf "$root"' EXIT
workspace="$root/workspace"
scratch="$root/attempt-scratch"
mkdir -p "$workspace" "$scratch"
cat > "$workspace/fixture.py" <<'PY'
value = "compiled"
PY
cat > "$workspace/fixture.c" <<'C'
int main(void) { return 0; }
C
mkdir -p "$workspace/module/include"
cat > "$workspace/module/include/Cached.h" <<'H'
static inline int cached_value(void) { return 0; }
H
cat > "$workspace/module/module.modulemap" <<'M'
module Cached { header "include/Cached.h" export * }
M
cat > "$workspace/fixture.m" <<'M'
@import Cached;
int main(void) { return cached_value(); }
M
denied="$root/outside-write.txt"
export RUNESTRA_ATTEMPT_SCRATCH="$scratch"
export TMPDIR="$scratch/tmp"
export TMP="$scratch/tmp"
export TEMP="$scratch/tmp"
export XDG_CACHE_HOME="$scratch/cache"
export PYTHONPYCACHEPREFIX="$scratch/cache/python"
export CARGO_TARGET_DIR="$scratch/build"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" "$PYTHONPYCACHEPREFIX" "$CARGO_TARGET_DIR"

cat > "$root/prompt.txt" <<PROMPT
Use your command execution tool to run these commands in order from the workspace:
1. Run python3 -m py_compile fixture.py, with PYTHONPYCACHEPREFIX=$PYTHONPYCACHEPREFIX.
2. Run clang fixture.c -o $CARGO_TARGET_DIR/fixture.
3. Run clang -fmodules -fmodules-cache-path=$scratch/cache/clang-modules -I module fixture.m -o $CARGO_TARGET_DIR/fixture.
4. Attempt to write the file $denied with python3. Do not use sudo, do not change sandbox settings, and do not retry the write by another route. Report the exact denied path and tool.
Return a short result that says whether steps 1 through 3 succeeded and whether step 4 was denied.
PROMPT

codex exec --ignore-user-config --skip-git-repo-check --json \
  --sandbox workspace-write --add-dir "$scratch" \
  --output-last-message "$root/result.txt" -C "$workspace" - \
  < "$root/prompt.txt" > "$root/events.jsonl"

test -s "$PYTHONPYCACHEPREFIX/$(python3 -c 'import sys; print("cpython-" + str(sys.version_info.major) + str(sys.version_info.minor))')/fixture.cpython-$(python3 -c 'import sys; print(str(sys.version_info.major) + str(sys.version_info.minor))').pyc" || \
  find "$PYTHONPYCACHEPREFIX" -name 'fixture*.pyc' -print -quit | grep -q .
test -x "$CARGO_TARGET_DIR/fixture"
find "$scratch/cache/clang-modules" -type f -print -quit | grep -q .
test ! -e "$denied"
grep -Eiq 'denied|not permitted|permission' "$root/result.txt"
if ! grep -Fq "$denied" "$root/result.txt"; then
  cat "$root/result.txt" >&2
  printf 'Expected denied path: %s\n' "$denied" >&2
  exit 1
fi
if ! grep -Fq 'python3' "$root/result.txt"; then
  cat "$root/result.txt" >&2
  echo "Expected the python3 tool in the structured report." >&2
  exit 1
fi
echo "PASS: cache and build writes succeeded in configured roots; outside write remained blocked."
