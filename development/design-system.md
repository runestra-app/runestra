# Web interface design system

This document defines the visual and interaction rules for the Runestra web console. New screens should use the same tokens, controls,
spacing, navigation, and status language.

## Direction

The console takes cues from GitHub and GitHub Actions: quiet neutral surfaces,
thin borders, compact lists, clear text hierarchy, and status colors reserved
for operational meaning. It should feel like a tool for inspecting and
operating workflows, not a marketing dashboard. Use familiar system fonts and
avoid decorative shadows, gradients, and oversized cards.

Support light and dark appearance. Follow the operating system setting by
default, remember an explicit user choice in local storage, and keep both modes
readable at the same density. Do not make status depend on color alone.

## Layout

- The top bar identifies the product and contains connection, theme, and session
  controls.
- The left rail contains projects, their workflows, and global engine actions.
- The main area uses a page heading, a small set of tabs, and a focused content
  surface.
- Project pages place run lists first. Workflow definitions remain one click
  away. Engine wide runner and resource information lives under System.
- On narrow screens, project and workflow navigation becomes horizontal and the
  main content uses the available width.
- Use whitespace to separate sections. Keep lists dense enough to scan several
  runs without scrolling through large cards.

## Tokens

`src/aorch/engine/web/styles.css` is the source of truth for CSS custom
properties. Components must use these semantic tokens instead of introducing
one off colors:

| Purpose | Token |
|---|---|
| Page background | `--canvas` |
| Subtle background | `--canvas-subtle` |
| Content surface | `--surface` |
| Raised surface | `--surface-raised` |
| Main text | `--fg` |
| Supporting text | `--fg-muted` |
| Quiet text | `--fg-subtle` |
| Links and focus | `--accent` |
| Success | `--success` and `--success-subtle` |
| Warning | `--warning` and `--warning-subtle` |
| Failure | `--danger` and `--danger-subtle` |
| Borders | `--border` and `--border-muted` |

The default corner radius is six pixels. Controls should use a one pixel
border, modest padding, and a visible keyboard focus outline. Reserve the green
primary button for the action that advances the current task.

## Typography and content

Use the platform system sans serif stack for interface text. Use a monospace
stack for commands, hashes, logs, and structured output. Page titles are the
strongest heading; section titles are compact and semibold. Secondary details
should stay short and muted. Buttons start with a verb, and status labels use
the same wording as the engine.

## Status and feedback

Pair each status color with its text and a small status mark. Use green for
successful or ready states, amber for waiting or active states, and red for
failed states. Neutral states remain neutral. Show connection errors in the
shared status area and keep field or project errors next to the affected
content. Empty states should say what is missing and give the next useful step.

## Interaction rules

- Keep project selection, workflow selection, starting a run, run cancellation,
  deletion, project refresh, and session controls available from the console.
- Destructive actions require a clear confirmation and name the affected data.
- Preserve keyboard access and visible focus for every interactive control.
- Use semantic buttons, links, headings, tables, and disclosure elements.
- Render data as text nodes. Do not interpolate server values into HTML strings.
- Keep the interface usable when there are no projects, no workflows, no runs,
  offline runners, or configuration errors.
- Prefer event updates, with a slower polling fallback. Avoid overlapping
  refresh requests and make reconnect state visible.

## Component patterns

- **Project row:** name, compact project status, selected background.
- **Workflow row:** branch marker and workflow name. Selected workflow uses the
  same accent surface as the selected project.
- **Run row:** run identifier, status, start time, total duration, and applicable controls. Run details separate execution, waiting, and reconciliation time.
- **Step node:** step name, status, executor type, and dependency links. Graph
  edges represent only declared `needs` relationships.
- **System table:** use for runners and resource locks, with concise column
  names and a clear empty state.
- **Details disclosure:** keep logs, events, and artifact downloads available
  in a readable activity list, with raw structured data behind a disclosure.

## Extending the interface

Before adding a new screen, reuse the existing page heading, tabs, buttons,
status pills, surfaces, and empty states. Add a new token only when an existing
semantic token cannot describe the role. Keep behavior in `app.js`, structure
in `index.html`, and visual rules in `styles.css`. Code and code comments remain
in English.
