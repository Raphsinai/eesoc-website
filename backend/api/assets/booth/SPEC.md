# EESoc photo booth print: implementation spec

This is the brief for building the EESoc photo booth print into the website.
The folder contains everything needed. The SVG templates are the source of truth for the design.
This document explains how to fill them and what the site must do around them.

---

## 1. What the site must produce

- A print-ready **PNG, 1181 × 1747 px, 300 dpi, sRGB**, for a **Canon SELPHY CP1500, Postcard size (100 × 148 mm), portrait, borderless**.
- The print can hold **1, 2, 3 or 4 photos**. Each count has its own template.
- Each print shows:
  - the event name, date and venue (these change per event);
  - a QR code linking to the digital copy of that print (this changes per print).
- Users must be able to **move and zoom each photo inside its window** before the print is generated.
- Reference renders of the finished result are in `reference/`, one per photo count.

## 2. Files

| Path | Purpose |
| --- | --- |
| `templates/booth-1-photo.svg` … `booth-4-photo.svg` | The four templates. Vector, with fonts embedded. Named element ids are listed in §4. |
| `template.json` | Every measurement in machine-readable form: page, colours, fonts, text, windows, separators. |
| `assets/eesoc-logo-white.svg` | The logo used in the templates (already inlined in each SVG). |
| `assets/fonts/*.ttf, *.woff2` | Space Mono Bold and IBM Plex Mono Regular. Use the TTFs for server-side rasterisers. |
| `reference/careers-fair-N-photo.jpg` | What a correct 300 dpi output looks like. Example event: "Careers Fair · Wed 7 Oct 2026 · EEE Building". |

## 3. Coordinate system

- Everything is in **design px** (1 px = 1/96 in). The page is **378 × 559**. Every SVG has the matching `viewBox="0 0 378 559"`.
- To get 300 dpi output pixels, **multiply by 3.125**. Scale uniformly; never stretch one axis.
- The origin is top-left. `y` grows downward.

## 4. Template anatomy (identical in all four SVGs, except the windows and separators)

Draw order, back to front:

| Element id | What | Geometry (design px) |
| --- | --- | --- |
| `background` | Page fill `#0B0D12` | 0, 0, 378 × 559 |
| `pins` | 19 gold rects along the top and bottom edges. They intentionally run off the paper. | 8 × 12 each, x = 5 + 20k (k = 0..18), y = 0 and y = 547 |
| `header-lines` | Two gold 1 px lines, one each side of the logo | (10, 42, 107 × 1) and (261, 42, 107 × 1) |
| `logo` | White EESoc wordmark with gold pins | 129, 30, 120 × 25 |
| `photo-N-window` | Empty window fill `#14171F`. It shows only if a photo is missing. | see §5 |
| `photo-N` | **Empty `<g>` clipped to the window. Insert the photo here.** | see §5 |
| `separators` | Gold 1 px lines between photos (layouts 2–4) | see §5 |
| `event-name` | Text, token `{{EVENT_NAME}}` | baseline (18, 494.61) |
| `event-details` | Text, token `{{EVENT_DETAILS}}` | baseline (18, 510.38) |
| `scan-text` | Fixed text "Scan for the digital copy" | baseline (18, 526.38) |
| `qr-tile` | Warm ivory rounded carrier with thin gold rim behind the QR | 300, 470, 62 × 62 |
| `qr` | **Empty `<g>`. Insert H-corrected QR modules, rounded finders and central E-chip here.** | centred within 300, 470, 62 × 62 |

Colours: background `#0B0D12`, gold `#E7B570`, empty window `#14171F`, title `#FFFFFF`, muted `#8A93A6`, QR ink `#0B0D12` on warm ivory `#F7F1E7`; gold `#E7B570` contacts and logo-chip outline.

## 5. Photo windows per layout

The photo area is x 18, y 73, 342 × 386. There is a 12 px gap between photos, with a 1 px gold line down the middle of each gap.
Every gold line extends 8 px past the photo area and stops ≥ 10 px from the paper edge, logo and footer.

### 1 photo — `templates/booth-1-photo.svg`

| Window | x | y | w | h | aspect (w/h) | 300 dpi px: left, top, w × h |
| --- | --- | --- | --- | --- | --- | --- |
| `photo-1` | 18 | 73 | 342 | 386 | 0.886 | 56, 228, 1069 × 1206 |

Separators: none.

### 2 photos — `templates/booth-2-photo.svg` (two rows)

| Window | x | y | w | h | aspect | 300 dpi px |
| --- | --- | --- | --- | --- | --- | --- |
| `photo-1` | 18 | 73 | 342 | 187 | 1.829 | 56, 228, 1069 × 585 |
| `photo-2` | 18 | 272 | 342 | 187 | 1.829 | 56, 850, 1069 × 584 |

Separators: horizontal (10, 265.5, 358 × 1).

### 3 photos — `templates/booth-3-photo.svg` (one wide on top, two below, rows 1.4 : 1)

| Window | x | y | w | h | aspect | 300 dpi px |
| --- | --- | --- | --- | --- | --- | --- |
| `photo-1` | 18 | 73 | 342 | 218.167 | 1.568 | 56, 228, 1069 × 682 |
| `photo-2` | 18 | 303.167 | 165 | 155.833 | 1.059 | 56, 947, 516 × 487 |
| `photo-3` | 195 | 303.167 | 165 | 155.833 | 1.059 | 609, 947, 516 × 487 |

Separators:
- horizontal (10, 296.667, 358 × 1);
- vertical (188.5, 296.667, 1 × 170.333). It starts on the horizontal line, so the two form a T.

### 4 photos — `templates/booth-4-photo.svg` (2 × 2)

| Window | x | y | w | h | aspect | 300 dpi px |
| --- | --- | --- | --- | --- | --- | --- |
| `photo-1` | 18 | 73 | 165 | 187 | 0.882 | 56, 228, 516 × 585 |
| `photo-2` | 195 | 73 | 165 | 187 | 0.882 | 609, 228, 516 × 585 |
| `photo-3` | 18 | 272 | 165 | 187 | 0.882 | 56, 850, 516 × 584 |
| `photo-4` | 195 | 272 | 165 | 187 | 0.882 | 609, 850, 516 × 584 |

Separators: horizontal (10, 265.5, 358 × 1) and vertical (188.5, 65, 1 × 402).

Photos are numbered left to right, then top to bottom.

## 6. Photo placement model (what the site stores per photo)

Each photo is placed by three numbers. The photo **always covers its whole window**, so any stored value is valid and no gaps are possible.

| Field | Range | Default | Meaning |
| --- | --- | --- | --- |
| `zoom` | ≥ 1 (cap at 8) | 1 | Extra zoom on top of the cover fit. |
| `x` | 0..1 | 0.5 | Horizontal pan. Same as CSS `object-position`: 0 = left edge, 0.5 = centre, 1 = right edge. |
| `y` | 0..1 | 0.5 | Vertical pan. 0 = top, 1 = bottom. |

Optionally, the site can store a `crop` (`{x, y, width, height}` in source pixels) when it already uses a cropper with the window's aspect ratio. In that case use the crop rect as the "image" in the formulas below.

### Formulas

Window `W = (Wx, Wy, Ww, Wh)` in design px. Source image `iw × ih` px, after EXIF rotation.

```
s    = max(Ww / iw, Wh / ih) × zoom        // scale, design px per source px
dw   = iw × s ;  dh = ih × s               // drawn size
left = Wx + (Ww − dw) × x                  // drawn position
top  = Wy + (Wh − dh) × y
```

**To place it in the SVG**, insert into `<g id="photo-N">`:

```xml
<image href="…photo…" x="{left}" y="{top}" width="{dw}" height="{dh}" preserveAspectRatio="none"/>
```

The group is already clipped to the window. With `zoom = 1, x = y = 0.5` this is the same as `preserveAspectRatio="xMidYMid slice"` on the window rect.

**For a raster pipeline** (e.g. sharp, Pillow, ImageMagick), use the visible part of the source:

```
srcLeft   = (Wx − left) / s
srcTop    = (Wy − top) / s
srcWidth  = Ww / s
srcHeight = Wh / s
```

Extract that rect, resize it to the window's 300 dpi size (§5), and composite it at the window's 300 dpi position.

### Interaction maths (for the editor)

The interactions work in design px:

- **Pointer → design px**: `X = (clientX − box.left) / box.width × 378`, `Y = (clientY − box.top) / box.height × 559`, where `box` is the preview's bounding rect.
- **Which window**: the first window whose rect contains (X, Y).
- **Drag by (dx, dy)**:
  - `x ← clamp01(x − dx / (dw − Ww))`
  - `y ← clamp01(y − dy / (dh − Wh))`
  - If `dw − Ww` (or `dh − Wh`) is ~0, keep that axis at 0.5.
- **Zoom by factor f around the point (ax, ay)**, for the wheel, pinch or a slider:
  1. `u = (ax − left) / dw`, `v = (ay − top) / dh`.
  2. Set `zoom ← clamp(zoom × f, 1, 8)` and recompute `dw`, `dh`.
  3. `left' = ax − u × dw`, `top' = ay − v × dh`.
  4. `x ← clamp01((Wx − left') / (dw − Ww))`, `y ← clamp01((Wy − top') / (dh − Wh))`.
- **Reset**: `zoom = 1, x = 0.5, y = 0.5`.

## 7. Text

- **`{{EVENT_NAME}}`**: plain event name, sentence or title case as entered (e.g. "Careers Fair").
  - Space Mono Bold 14 px, letter-spacing −0.28 px, white.
- **`{{EVENT_DETAILS}}`**: `"{date} · {venue}"`, **converted to UPPERCASE before inserting** (e.g. "WED 7 OCT 2026 · EEE BUILDING").
  - The separator is a middle dot (U+00B7) with a space on each side.
  - IBM Plex Mono Regular 9 px, letter-spacing 1.26 px, gold.
- **Escape** `&`, `<`, `>` (and quotes) before inserting text into the SVG.
- **Maximum width is 276 px** for each line, so it never reaches the QR tile. If a line is wider:
  1. Shrink the font in 0.5 px steps, down to 10 px (name) or 7 px (details). Keep the baseline fixed.
  2. If it still doesn't fit, cut characters and end with "…".
- The baselines are fixed (§4). Do not re-centre the text.

## 8. QR code

- **Content**: the URL of that print's digital copy.
  - Keep it short (about 25 characters or fewer) so the code stays coarse enough to scan on a 13.8 mm tile.
- **Error correction**: H, reserving enough redundancy for a small central EESoc chip emblem.
- **Placement**: centre the code on the 62 × 62 carrier at (300, 470), keeping 4 modules of quiet space on every side. The carrier has softened corners and short gold contacts *outside* the quiet zone.
- **Central chip**: a 9-module ivory knock-out, gold inner chip outline/contacts and the actual first E path of the EESoc wordmark in navy. The visual mark is deliberately small so scanner alignment/error correction remains effective.
- **Sharpness**: snap each module boundary to an output pixel at 300 dpi (do not antialias data modules); only the finder and logo edges are rounded/smoothed.
- **Ink**: `#0B0D12` on `#F7F1E7`. Keep print and live SVG geometry in sync and test decoding at final printed size.

## 9. Website behaviour

1. **Pick a layout**: 1, 2, 3 or 4 photos. The number of photos taken or uploaded decides it.
2. **Fill the windows**:
   - Default each photo to `zoom 1, x 0.5, y 0.5`.
   - Show a live preview built from the same template (an inline SVG is easiest).
3. **Adjust**:
   - drag to pan;
   - wheel or pinch to zoom around the pointer;
   - double-click to reset;
   - provide slider fallbacks for zoom, x and y, for keyboard and accessibility.
4. **Generate**: render the final PNG at 1181 × 1747 using the stored values. Never use a screenshot of the preview.
5. **Store** per print (enough to re-render it identically):

```json
{
  "layout": 3,
  "event": { "name": "Careers Fair", "date": "Wed 7 Oct 2026", "venue": "EEE Building" },
  "qrUrl": "https://…/p/abc123",
  "photos": [
    { "src": "…/1.jpg", "zoom": 1,   "x": 0.5, "y": 0.45 },
    { "src": "…/2.jpg", "zoom": 1.3, "x": 0.5, "y": 0.42 },
    { "src": "…/3.jpg", "zoom": 1,   "x": 0.5, "y": 0.65 }
  ]
}
```

Event name, date and venue are set once per event and reused for every print.

## 10. Rendering notes and pitfalls

- **Fonts**:
  - Browsers: the fonts are embedded in the SVGs as base64 woff2, so the SVG renders correctly inline, as `<img>`, or drawn to a canvas.
  - Server-side SVG rasterisers (resvg, librsvg/sharp, Inkscape, CairoSVG) usually ignore embedded web fonts. Register or install `assets/fonts/*.ttf` with the family names **"Space Mono"** (weight 700) and **"IBM Plex Mono"** (weight 400). Otherwise the text falls back to a different monospace font.
- **Rasterise at 300 dpi**: render the SVG at width 1181 px (or `density` 300 in sharp/ImageMagick). Do not rasterise small and upscale.
- **EXIF orientation**: apply it before measuring `iw × ih`, especially for phone photos. Browsers do this automatically; in sharp, call `.rotate()`.
- **Colour**: export sRGB PNG. Do not add colour profiles or convert to CMYK.
- **Borderless trim**: the printer trims a little off each edge. Only the pins reach the edge, by design. Do not move anything closer to the edge.
- **Do not change**:
  - the geometry, colours, line thicknesses or font sizes beyond the shrink-to-fit rule in §7;
  - the gold lines stopping short of the paper edge;
  - the 1 px separators.

## 11. Acceptance checks

1. The output PNG is exactly 1181 × 1747 for every layout.
2. With the example inputs, each layout visually matches `reference/careers-fair-N-photo.jpg`.
   - Example inputs: "Careers Fair", "Wed 7 Oct 2026", "EEE Building", any photos.
   - Logo, lines, pins, text baselines and the QR tile should land in the same place to within 1–2 px.
3. A photo dragged to any corner, at any zoom, never leaves an empty strip inside its window.
4. Re-rendering a stored print gives an identical image.
5. A long event name shrinks and then ends with "…", and never overlaps the QR tile.
6. The printed QR scans from a phone at arm's length.

## 12. Licences

- Space Mono and IBM Plex Mono: SIL Open Font License 1.1 (`assets/fonts/OFL-*.txt`). Embedding and serving them is allowed.
- The EESoc logo is the society's own mark.
