# Careers fair — company logos

Pulled 2026-09-30. Vector `*.svg` is the master; `png/*.png` are 1600px-wide
transparent-background exports rendered from those SVGs for tools that won't take SVG.

| File | Company | Source | Notes |
|---|---|---|---|
| `optiver.svg` | Optiver | Wikimedia Commons — *Optiver Logo.svg* | Black wordmark + orange triangle |
| `dca-design.svg` | DCA Design International | dca-design.com header (inline SVG, extracted) | Site version is white-on-dark; recoloured to black. `dca-design-white.svg` is the original white for dark backgrounds |
| `apple.svg` | Apple | Wikimedia Commons — *Apple logo black.svg* | Mark only (no wordmark) |
| `jump-trading.svg` | Jump Trading | Wikimedia Commons — *Jump-trading-primary-…-red-and-black-full-color-rgb.svg* | Current primary lockup. `jump-trading-alt-circlemark.svg` is the older circle-mark variant |
| `cirrus-logic.svg` | Cirrus Logic | Wikimedia Commons — *Cirrus Logic 2023 logo.svg* | Blue cloud mark + italic serif wordmark |
| `qualcomm.svg` | Qualcomm | Wikimedia Commons — *Qualcomm-Logo.svg* | Current (post-2018) blue wordmark |
| `entangl.svg` | Entangl | entangl.com — `/assets/entangl-wordmark.svg` | Official site asset. Dark-teal wordmark + mark |
| `goldman-sachs.svg` | Goldman Sachs | Wikimedia Commons — *Goldman Sachs 2022 Black.svg* | Stacked two-line wordmark |
| `png/technelec.png` | Technelec | technelec.co.uk (Squarespace CDN) | **PNG only — no SVG available.** 2500×833, navy mark + wordmark. `png/technelec-white.png` is the reversed version for dark backgrounds; `png/technelec-mark-white.png` is the mark alone (white, 1200×1200) |
| `jane-street.svg` | Jane Street | janestreet.com — `/assets/logo_horizontal-….svg` | Official site asset, horizontal lockup, black. The supplied `jane_street_logo_horizontal_*_registered.ai/.png` are the brand-pack originals (with ®) and are kept alongside — the `.ai` files are PDF-compatible |
| `imc.svg` | IMC | imc.com (inline SVG in the page header, extracted) | Dot-grid mark + lowercase `imc` wordmark. Shipped as `fill="currentColor"`; pinned to black. `imc-white.svg` is the reversed version |
| `qrt.svg` | Qube Research & Technologies (QRT) | qube-rt.com — `/img/qrt.svg`, via the Wayback Machine | Blue cube mark + wordmark. Site version is white-on-dark; recoloured to black. `qrt-white.svg` is the original white for dark backgrounds |
| `arctic-lake.svg` | Arctic Lake Technology | arcticlake.com — `/assets/AL_Logo-removebg-preview-2_….png`, **traced to vector** | The site only ships a 716×348 PNG (no SVG anywhere, favicon aside). Traced with potrace from a 6× upscale, two colour layers: navy `#465687` (ARCTIC + chevron), light blue `#7BA7D8` (LAKE). Faithful at poster size, but not an official vector — ask them for their brand pack if it matters. `arctic-lake-white.svg` is all-white for dark backgrounds; the untouched source is `png/arctic-lake-original-716px.png` |
| `ducky-drones.svg` | Ducky Drones | duckydrones.com — duck mark from `framerusercontent.com/images/wBMlYc5xZ9dw4Y11VAJWEvCc99U.svg`; wordmark **built** | The site has no lockup file: its header is the duck plus the live text "ducky drones" in **Afacad Bold** (Google Fonts, OFL). This lockup sets that text in Afacad Bold at its −0.03em tracking and converts it to outlines. Default is black text, and the duck has a black outline (as in their favicon) so it holds up on white. `ducky-drones-yellow.svg` (all `#FFD600`, how their site looks) and `ducky-drones-white.svg` are for dark backgrounds. `ducky-drones-mark.svg` / `-mark-outlined.svg` are the duck alone |

## Fixes applied

- `optiver`, `jump-trading`, `apple` had `width`/`height` but no `viewBox`, so they
  wouldn't scale in browsers or design tools. Added a matching `viewBox` to each.
- `dca-design` was `fill="white"` (it sits on a dark site header) — invisible on a
  light poster. Black version is the default; white kept alongside.
- `qrt` had the same problem (white wordmark over a dark hero) and got the same
  treatment: black is the default, white kept alongside.
- qube-rt.com returns 403 to anything that is not a browser, including for its
  own static assets, so `qrt.svg` came from the Wayback Machine's copy of it.
- `qrt` painted every fill through `class="cls-N"` plus a `<style>` block.
  Anything that does not run CSS — PyMuPDF, several design tools, some PDF
  pipelines — dropped the lot and rendered the whole logo flat black. The fills
  are now inline `fill=` attributes, so the file stands on its own.
- `png/*.png` for `imc`, `qrt` and `jane-street` were rendered from the SVGs at
  1600px wide with PyMuPDF, matching the rest of the folder.
- Arctic Lake and Ducky Drones added 2026-10-07; their PNGs were rendered the same way.
  The duck's eye is a hole, so it shows whatever background the logo sits on.

## Before publishing

These are all third-party trademarks. Normal practice for a sponsor/attendee poster,
but worth confirming against each company's brand guidelines — Apple in particular is
strict about how its mark may be used, and several sponsors supply an official asset
pack on request that will beat a scraped file.
