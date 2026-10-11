# Development

Requires Git and `uv`. Python 3.12 or newer is resolved by `uv` when needed.
From the repository root:

```sh
uv sync --locked --all-groups
```

Start the browser and real local runner with the [quick start](../README.md#quick-start).
Use a separate port and private directory when an existing engine is running:

```sh
bash scripts/quick-start.sh --port 8875 --directory .runtime/development
```

## Checks

Run the checks relevant to a change. The complete Python suite and static checks
are:

```sh
uv run --locked --group dev python -m pytest -q
uv run --locked ruff check src tests scripts
node --check src/aorch/engine/web/app.js
bash -n scripts/quick-start.sh scripts/install-local.sh scripts/install-runner.sh scripts/uninstall-local.sh
```

Validate a changed workflow or inspect the action contract before executing it:

```sh
uv run --locked aorch validate examples/local-actions
uv run --locked aorch validate examples/public-mvp/manual-review.yaml
uv run --locked aorch actions list
uv run --locked aorch actions describe github.open_pr --json
```

Use [architecture](architecture.md) to locate ownership of system behavior,
user guides under `../docs/` for public contracts, and
[the design system](design-system.md) for browser changes.

## Documentation changes

Keep the README and user guides focused on setup and operation. Put source-code
development rules in `AGENTS.md`; keep architecture notes, interface rules,
proposals and historical verification under `development/`. Design proposals
must be marked as proposals. Record implementation checks with date,
environment, scenario and limits under `development/evidence/`. Put supported
configuration behavior in the user reference and update its schemas when the
configuration contract changes.

## Contributions and licensing

Runestra is distributed under Apache-2.0. By submitting material for inclusion
in Runestra, contributors submit it under Apache-2.0 unless they agree otherwise
with the project owner. Only submit material you have the right to contribute;
identify third-party material and its license. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for runtime dependencies.
