# Runestra visual identity, working guide

**Status:** selected editable SVG masters and digital brand kit are prepared; legal similarity review is still open.

## Mark

The selected symbol uses three slanted strokes with a continuous negative-space loop through them. The three strokes represent ordered workflow stages; the loop represents a process that can return and run again. Keep the mark abstract. Do not add an R, rune, node circles, branch diagrams, or a central arrow.

The standalone mint, dark, and light SVGs are the source artwork. The lockup is a working horizontal composition. Its wordmark uses live Inter text with Arial and sans-serif fallbacks, so convert that text to outlines before producing a font-independent distribution export. The app header uses the mint symbol on dark surfaces and dark symbol on light surfaces.

The SVG artwork uses editable path geometry and contains no bitmap. The loop is too fine to read at 16 px; use the supplied square favicon framing there, and use the standalone mark at 32 px or above. The favicon SVG and multi-size ICO are exported and checked.

## Color proposals

| Role | Value | Use |
|---|---|---|
| Ink | `#0B1220` | Main dark page background |
| Slate | `#111827` | Raised dark surface |
| Mint | `#75DFBD` | Brand mark and primary action on dark surfaces |
| Light | `#F3F5F4` | Main text and inverse mark |
| Muted | `#A3AFAC` | Supporting text, after contrast check |
| Border | `#29333A` | Quiet dividers and boundaries |
| Dark ink | `#17201E` | Dark mark on light backgrounds |

These values are proposals for the public identity, not application status tokens. Success, warning, failure, and waiting states retain their distinct meanings and should never depend on mint alone.

## Type and layout proposals

- Use Inter for headings and body copy, with a system sans-serif fallback.
- Use the existing monospace stack for commands, code, hashes, and logs.
- Keep body text readable and concise. Use generous spacing around landing-page sections and compact spacing inside product evidence.
- Use thin borders and neutral surfaces. Avoid large shadows, decorative gradients, and a card around every content block.
- Use mint for one primary action at a time. Links, focus, and operational status should retain clear contrast and their own roles.

## Product and marketing boundary

The public landing page may use a dark-only brand treatment. The Runestra console continues to support light and dark themes and keeps the existing semantic status colors. Use the actual local application as product evidence; generated interface studies are not product screenshots.

## Current limitations

The current mark is an editable vector interpretation of the selected concept. Its loop has been widened for small interface use, but it remains dense below 32 px. The wordmark is live text, the digital palette has a recorded static contrast check, and no legal or third-party mark similarity review has been completed. See the packaged exports and final use guidance in [`../../../Runestra-product/visual-identity/`](../../../Runestra-product/visual-identity/README.md).
