"""Print the supplied EESoc SVG photobooth design at exact SELPHY postcard size.

The overlay PNGs are faithfully rasterised, once, from the handoff SVGs (not
redrawn in this service). Only the four photo windows, two editable text lines,
and the QR modules are dynamic. Geometry comes directly from template.json.
"""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode
import regex

ROOT = Path(__file__).resolve().parent.parent / 'assets' / 'booth'
DPI = 300
SCALE = DPI / 96
INK = '#0B0D12'
WHITE = '#FFFFFF'
GOLD = '#E7B570'
MUTED = '#8A93A6'


@lru_cache(maxsize=1)
def spec() -> dict:
    return json.loads((ROOT / 'template.json').read_text())


@lru_cache(maxsize=4)
def overlay(count: int) -> Image.Image:
    with Image.open(ROOT / f'overlay-{count}.png') as source:
        return source.convert('RGBA')


def normalized_placement(value: dict | None) -> dict[str, float]:
    value = value or {}
    result = {}
    for key, default, minimum, maximum in (
        ('zoom', 1.0, 1.0, 8.0),
        ('x', 0.5, 0.0, 1.0),
        ('y', 0.5, 0.0, 1.0),
    ):
        candidate = float(value.get(key, default))
        if not math.isfinite(candidate) or not minimum <= candidate <= maximum:
            raise ValueError(f'Invalid photo {key}')
        result[key] = candidate
    return result


def source_region(slot: dict, image_size: tuple[int, int], place: dict | None = None) -> tuple[float, float, float, float]:
    """Source pixels covering the entire slot, applying the handoff's formula."""
    position = normalized_placement(place)
    iw, ih = image_size
    if iw < 1 or ih < 1:
        raise ValueError('Invalid photo dimensions')
    cover = max(slot['w'] / iw, slot['h'] / ih) * position['zoom']
    dw, dh = iw * cover, ih * cover
    left = slot['x'] + (slot['w'] - dw) * position['x']
    top = slot['y'] + (slot['h'] - dh) * position['y']
    sw, sh = slot['w'] / cover, slot['h'] / cover
    sx = (slot['x'] - left) / cover
    sy = (slot['y'] - top) / cover
    # Floating-point arithmetic at exact edges must not sample past the image.
    sx = max(0., min(float(iw) - sw, sx))
    sy = max(0., min(float(ih) - sh, sy))
    return (sx, sy, sx + sw, sy + sh)


def _clusters(value: str) -> list[str]:
    return regex.findall(r'\X', value)


def _emoji_tile(value: str, size: int) -> Image.Image:
    from services.photo_print import _emoji_tile
    return _emoji_tile(value, size)


def _is_emoji(value: str) -> bool:
    from services.photo_print import EMOJI_CLUSTER
    return bool(EMOJI_CLUSTER.search(value))


def _text_metrics(draw: ImageDraw.ImageDraw, clusters: list[str], font: ImageFont.FreeTypeFont, tracking: float) -> float:
    if not clusters:
        return 0.0
    width = sum(_emoji_tile(v, font.size).width if _is_emoji(v) else draw.textlength(v, font=font) for v in clusters)
    return width + max(0, len(clusters)-1) * tracking


def _fit_text(draw: ImageDraw.ImageDraw, value: str, path: Path,
              start_size: float, min_size: float, tracking: float, maxwidth: float):
    value = ' '.join(value.split())
    clusters = _clusters(value)
    for step in range(round((start_size-min_size)*2)+1):
        size = start_size - step * .5
        font = ImageFont.truetype(path, max(1, round(size*SCALE)))
        spacing = tracking * SCALE
        if _text_metrics(draw, clusters, font, spacing) <= maxwidth:
            return clusters, font, spacing
    if not clusters:
        return clusters, font, spacing
    while clusters and _text_metrics(draw, clusters + ['…'], font, spacing) > maxwidth:
        clusters.pop()
    while clusters and clusters[-1].isspace():
        clusters.pop()
    return clusters + ['…'], font, spacing


def _draw_text(page: Image.Image, draw: ImageDraw.ImageDraw, text: str, *, x: float, baseline: float,
               font_name: str, size: float, minimum: float, tracking: float,
               color: str, maxwidth: float = 276) -> None:
    font_path = ROOT / 'fonts' / ('SpaceMono-Bold.ttf' if font_name == 'title' else 'IBMPlexMono-Regular.ttf')
    clusters, font, spacing = _fit_text(draw, text, font_path, size, minimum, tracking, maxwidth*SCALE)
    pen = x * SCALE
    baseline_px = baseline * SCALE
    for cluster in clusters:
        if _is_emoji(cluster):
            tile = _emoji_tile(cluster, font.size)
            page.paste(tile, (round(pen), round(baseline_px - tile.height*0.86)), tile)
            pen += tile.width + spacing
        else:
            draw.text((round(pen), round(baseline_px)), cluster, fill=color,
                      font=font, anchor='ls')
            pen += draw.textlength(cluster, font=font) + spacing


# Place the code directly in the 52px white tile, not the handoff's
# conservative 44px code box. Short links use a tight two-module margin;
# denser links get a little more white space to stay easy to scan. The three
# finder targets have rounded corners; data modules stay crisp squares.
QR_SHORT_QUIET_MODULES = 2
QR_DENSE_QUIET_MODULES = 3.5
QR_DENSE_THRESHOLD = 33
QR_FINDER_RADIUS_MODULES = 0.8
QR_DENSE_FINDER_RADIUS_MODULES = 0.4


def _draw_qr(draw: ImageDraw.ImageDraw, url: str) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    tile = spec()['fixed']['qrTile']
    dense = modules > QR_DENSE_THRESHOLD
    quiet = QR_DENSE_QUIET_MODULES if dense else QR_SHORT_QUIET_MODULES
    finder_radius = QR_DENSE_FINDER_RADIUS_MODULES if dense else QR_FINDER_RADIUS_MODULES
    pitch = min(tile['w'], tile['h']) * SCALE / (modules + 2*quiet)
    if pitch < 2.5:
        raise ValueError('QR URL too long for printable tile')

    # Snap the cumulative module boundaries to output pixels instead of
    # truncating the pitch. This fills the tile without antialiasing the code.
    origin_x = (tile['x'] + (tile['w'] - modules*pitch/SCALE)/2) * SCALE
    origin_y = (tile['y'] + (tile['h'] - modules*pitch/SCALE)/2) * SCALE
    xs = [round(origin_x + index*pitch) for index in range(modules+1)]
    ys = [round(origin_y + index*pitch) for index in range(modules+1)]
    finders = ((0, 0), (modules-7, 0), (0, modules-7))

    def is_finder(x: int, y: int) -> bool:
        return any(ax <= x < ax+7 and ay <= y < ay+7 for ax, ay in finders)

    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark and not is_finder(x, y):
                draw.rectangle((xs[x], ys[y], xs[x+1]-1, ys[y+1]-1), fill=INK)

    # Slightly rounded finder corners make the code feel intentional. The
    # complete 7/5/3 finder geometry stays intact for scanning.
    for fx, fy in finders:
        for inset, span, color, radius in (
            (0, 7, INK, finder_radius),
            (1, 5, WHITE, finder_radius*.75),
            (2, 3, INK, finder_radius*.6),
        ):
            x0, y0 = xs[fx+inset], ys[fy+inset]
            x1, y1 = xs[fx+inset+span]-1, ys[fy+inset+span]-1
            draw.rounded_rectangle((x0, y0, x1, y1),
                                   radius=max(1, round(pitch*radius)), fill=color)


def render(photos: list[bytes], title: str, url: str, orientation: str = 'portrait',
           print_label: str | None = None, placements: list[dict] | None = None,
           event_date: str = '', event_venue: str = '', qr_url: str | None = None) -> bytes:
    if not 1 <= len(photos) <= 4:
        raise ValueError('A print needs 1–4 photos')
    if orientation not in ('portrait', 'landscape'):
        raise ValueError('Unknown print orientation')
    # This handoff is a portrait-only postcard, including for legacy landscape sets.
    definition = spec()
    width, height = definition['output']['width'], definition['output']['height']
    page = Image.new('RGB', (width, height), INK)
    windows = definition['layouts'][str(len(photos))]['windows']
    frame = placements or []
    for index, (data, window) in enumerate(zip(photos, windows)):
        with Image.open(BytesIO(data)) as source:
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert('RGB')
        crop = source_region(window, image.size, frame[index] if index < len(frame) else None)
        rect = window['px300']
        # EXTENT samples fractional source pixels without rounding the crop to
        # an integer boundary, so no blank strip appears at the window edges.
        fitted = image.transform((rect['width'], rect['height']), Image.Transform.EXTENT,
                                 crop, resample=Image.Resampling.BICUBIC)
        page.paste(fitted, (rect['left'], rect['top']))
    page.paste(overlay(len(photos)), (0, 0), overlay(len(photos)))
    draw = ImageDraw.Draw(page)
    _draw_text(page, draw, print_label or title, x=18, baseline=494.61,
               font_name='title', size=14, minimum=10, tracking=-.28, color=WHITE)
    details = ' · '.join(v for v in (event_date.strip(), event_venue.strip()) if v).upper()
    _draw_text(page, draw, details, x=18, baseline=510.38,
               font_name='details', size=9, minimum=7, tracking=1.26, color=GOLD)
    _draw_text(page, draw, 'Scan for the digital copy', x=18, baseline=526.38,
               font_name='details', size=9, minimum=9, tracking=0, color=MUTED)
    _draw_qr(draw, qr_url or url)
    out = BytesIO()
    page.save(out, 'PNG', dpi=(DPI, DPI), optimize=True)
    return out.getvalue()
