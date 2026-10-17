# Prototype Instructions

The selected brand is the dark technical Signalni tok identity. Reuse the final vector mark and real Runestra product captures, including on the engine interface; do not restore the generated mock logo or use generated interface screenshots as product evidence.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Keep the hero GitHub link paired with GitHub's official Invertocat SVG, used unchanged. Reference the current brand favicon through versioned asset paths so browsers pick up visual updates. The product proof section uses genuine local engine captures in a three-slide gallery with small, quiet previous, next, play/pause controls and direct slide controls. Rotate every seven seconds only while visible, and pause for pointer hover, keyboard focus, hidden tabs, or reduced-motion preference.

Umami tracking is enabled only on `runestra.app` and `www.runestra.app`; keep local and review previews excluded. Track aggregate page use and named product-navigation actions only. Never send names, email addresses, command content, or other visitor-entered data to analytics.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
