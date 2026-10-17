# Landing page design QA

**Date:** 12 October 2026<br>
**Result:** passed for local review<br>
**Direction:** Workflow in view, dark Signalni tok identity, mint accent

## Visual comparison

- Selected design source: [`/Users/Shared/git/Runestra-product/assets/landing-directions/workflow-in-view.png`](../../Runestra-product/assets/landing-directions/workflow-in-view.png), 854 × 1840 px.
- Desktop implementation capture: [`verification/landing-desktop.png`](verification/landing-desktop.png), 854 px wide, 2760 px full-page height, captured in Chromium at a 854 × 1840 px viewport.
- Matched comparison: [`verification/design-comparison.png`](verification/design-comparison.png), 1708 × 1840 px. It places the selected design beside the first 1840 px of the full-page implementation at the same 854 px width.
- Mobile implementation capture: [`verification/landing-mobile.png`](verification/landing-mobile.png), 390 px wide, 3250 px full-page height, captured at a 390 × 844 px viewport.
- Product evidence: `public/brand/product-run.png`, `product-run-detail.png`, and `product-workflows.png`, each 1280 × 700 px. They show genuine engine views with the updated mark.

The implementation keeps the chosen headline, Git to engine to runner sequence, real product proof and Quick Start hierarchy. Copy and labels use current product facts, the mock's unverified Community link is removed, and generated interface imagery is replaced by the local application. The selected source is more compact because it omits the release-status note and FAQ. Those details remain below the main sections to answer practical setup and runner-security questions. The full-page implementation is therefore taller than the selected study; the first 1840 px comparison uses equal viewport width and height.

## Browser and behavior checks

- Landing page returned HTTP 200 and loaded without JavaScript exceptions or console errors.
- Viewport widths 360, 390, 768, 854 and 1440 px had no horizontal overflow.
- The Quick Start button copied the exact three shell commands and displayed “Copied.”
- Native FAQ disclosure opened. The mobile navigation opened, and its Get started link navigated to `#quick-start` and closed the menu.
- Local app browser inspection showed the mint mark in dark mode and the dark mark in light mode. The standalone mark was checked at 24, 32 and 48 px. The versioned favicon and brand SVG assets returned HTTP 200.
- The three-slide product gallery uses the local run overview, run detail and workflow list.
- `npm run build`, `npm run test:sites` (4 passing tests), targeted engine UI test (1 passing test), wheel build, and `git diff --check` passed.

## Remaining launch work

This is a local review build, not a public deployment. The domain, hosting target, canonical URL and absolute Open Graph image URL are not set. The logo follows the selected concept and is available as editable SVG; trademark and third-party similarity review remains open. Use the favicon at 16 px and the standalone mark at 24 px or above.

**Final result: passed.**

## Logo and gallery update

The hero GitHub link now uses GitHub's official Invertocat SVG. The site and engine reference the refreshed favicon under `/brand/favicon-v2.svg` and `/static/favicon-v2.svg` so browsers request the updated asset. The gallery rotates every seven seconds while in view and supports direct selection, previous/next navigation, and pause/resume with small, quiet controls. It pauses on hover, keyboard focus, hidden tabs, and reduced-motion preference.

The refreshed page was reviewed in a browser at desktop and 390 px mobile widths. Manual selection showed the matching screenshot and caption, and automatic rotation advanced while visible. The website production build passed. The Sites test suite was not rerun for this gallery update.
