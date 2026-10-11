# Instructions for coding agents

## Scope

These instructions apply to changes in this repository. Product and user guides
belong under `docs/`. Contributor instructions, architecture notes, interface
design rules, development proposals and historical records belong under
`development/`.

## Source of truth

- Project YAML fields and cross-field validation are defined in
  `src/aorch/config/models.py` and `src/aorch/config/loader.py`.
- Engine and runner YAML fields are defined in `src/aorch/config/service.py`.
- Built-in action inputs, outputs and contract versions are defined in
  `src/aorch/extensions/schemas.py`.
- Runner behavior is authoritative for claims about execution, credentials,
  permissions, environment variables and external effects.
- Update the user reference and its JSON Schema files when a supported public
  configuration contract changes. Never document proposed behavior as shipped.

## Changes

- Preserve existing user changes. Inspect the working tree before editing and
  do not revert unrelated modifications.
- Keep source code and code comments in English.
- Keep credentials out of project configuration, examples, logs and generated
  snapshots. Use explicit approval steps for workflow actions that publish or
  otherwise change external systems.
- Keep examples runnable and identify the configuration version they require.
- Keep user setup instructions in `docs/`. Put instructions about changing
  Runestra's source code in `development/` or in this file.
- Do not publish the local plan in `.local/roadmap.md`. It is excluded in this
  checkout's `.git/info/exclude` file and must stay outside `docs/`.

## Documentation checks

When changing documentation or schemas, validate YAML examples with
`aorch validate`, inspect generated schemas for drift, and check relative links.
Update the affected user guide in the same change as the behavior it describes.
