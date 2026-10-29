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
QR_PAPER = '#F7F1E7'
QR_GOLD = '#E7B570'


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


# Warm ivory chip package. Browser SVG and PNG output both use the same
# 62-unit carrier, H correction, 4-module quiet zone and 9-module E badge.
QR_QUIET_MODULES = 4
QR_BADGE_MODULES = 9
QR_CHIP_MODULES = 6.45
QR_MARK_W_MODULES = 4.6
QR_MARK_H_MODULES = 3.8


@lru_cache(maxsize=1)
def _qr_e_mask() -> Image.Image:
    # Generated from the actual first E in the EESoc wordmark by
    # scripts/build_booth_qr_e.mjs; no font substitution on the server.
    with Image.open(ROOT / 'qr-e-mask.png') as image:
        return image.convert('L')


def _draw_qr(draw: ImageDraw.ImageDraw, url: str) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    tile = spec()['fixed']['qrTile']
    pitch = min(tile['w'], tile['h']) * SCALE / (modules + 2*QR_QUIET_MODULES)
    if pitch < 2.5:
        raise ValueError('QR URL too long for printable tile')

    # Snap each module boundary to the nearest 300dpi pixel; modules should
    # never be blurred on a real SELPHY print.
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

    # Rounded, but otherwise perfectly standard 7/5/3 finder targets.
    for fx, fy in finders:
        for inset, span, color, radius in (
            (0, 7, INK, .65),
            (1, 5, QR_PAPER, .46),
            (2, 3, INK, .3),
        ):
            x0, y0 = xs[fx+inset], ys[fy+inset]
            x1, y1 = xs[fx+inset+span]-1, ys[fy+inset+span]-1
            draw.rounded_rectangle((x0, y0, x1, y1),
                                   radius=max(1, round(pitch*radius)), fill=color)

    # An opaque 9-module clearing protects the emblem from neighbouring data.
    # H correction absorbs this deliberately small missing portion of the code.
    cx = origin_x + modules*pitch/2
    cy = origin_y + modules*pitch/2
    half = QR_BADGE_MODULES*pitch/2
    draw.rounded_rectangle((round(cx-half), round(cy-half),
                            round(cx+half), round(cy+half)),
                           radius=round(.95*pitch), fill=QR_PAPER)

    # Six fine gold contacts and a bordered miniature processor package.
    chip_half = QR_CHIP_MODULES*pitch/2
    for offset in (-1.85, 0, 1.85):
        y = round(cy+offset*pitch)
        for side in (-1, 1):
            x1 = round(cx+side*(chip_half+.85*pitch))
            x2 = round(cx+side*(chip_half-.08*pitch))
            draw.line([(x1,y),(x2,y)], fill=QR_GOLD,
                      width=max(1,round(.35*pitch)))
    draw.rounded_rectangle((round(cx-chip_half), round(cy-chip_half),
                            round(cx+chip_half), round(cy+chip_half)),
                           radius=round(.75*pitch), fill=QR_PAPER,
                           outline=QR_GOLD, width=max(1,round(.32*pitch)))

    width = max(1,round(QR_MARK_W_MODULES*pitch))
    height = max(1,round(QR_MARK_H_MODULES*pitch))
    mask = _qr_e_mask().resize((width,height), Image.Resampling.LANCZOS)
    draw.bitmap((round(cx-width/2), round(cy-height/2)), mask, fill=INK)


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
