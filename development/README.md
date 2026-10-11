# Development notes

This directory contains material for contributors and coding agents. Product
setup and operation guides are under [`docs/`](../docs/README.md).

- [`contributing.md`](contributing.md): environment and checks for contributors.
- [`architecture.md`](architecture.md): internal component boundaries and
  implementation details.
- [`design-system.md`](design-system.md): web console design conventions.
- [`design/`](design/): proposals and contracts for planned changes.
- [`archive/`](archive/): superseded planning documents and historical records.
- [`evidence/`](evidence/): dated verification notes.

Repository-specific coding agent instructions are in [`AGENTS.md`](../AGENTS.md).
Regenerate the committed editor schemas after changing a configuration contract
with `uv run --locked python scripts/export-json-schemas.py`.
