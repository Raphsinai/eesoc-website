"""EESoc premium navy/gold 300 DPI 6x4 event prints using official pins logo."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode

NAVY = "#071D47"
BLUE = "#1049B1"
GOLD = "#E7B570"
IVORY = "#F5F3EE"
SOFT = "#D8E3FA"
QUIET = "#FDF8F0"
LOGO = Path(__file__).resolve().parent.parent / "assets" / "eesoc-pins-light.png"


@lru_cache(maxsize=48)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size=size)


def _fit_title(
    draw: ImageDraw.ImageDraw, title: str, maxwidth: int, size: int,
) -> tuple[str, ImageFont.ImageFont]:
    title = " ".join(title.split()) or "EESoc"
    font = _font(size, bold=True)
    while draw.textbbox((0, 0), title, font=font)[2] > maxwidth and size > 25:
        size -= 2
        font = _font(size, bold=True)
    if draw.textbbox((0, 0), title, font=font)[2] > maxwidth:
        while title and draw.textbbox((0, 0), title + "...", font=font)[2] > maxwidth:
            title = title[:-1]
        title += "..."
    return title, font


def layout(count: int, area: tuple[int, int, int, int], gap: int = 18) -> list[tuple[int, int, int, int]]:
    if count not in (1, 2, 3, 4):
        raise ValueError("A print needs 1–4 photos")
    x, y, w, h = area
    if count == 1:
        return [(x, y, w, h)]
    if count == 2:
        half = (h - gap) // 2
        return [(x, y, w, half), (x, y+half+gap, w, h-half-gap)]
    halfw = (w-gap)//2
    halfh = (h-gap)//2
    if count == 3:
        return [
            (x, y, w, halfh),
            (x, y+halfh+gap, halfw, h-halfh-gap),
            (x+halfw+gap, y+halfh+gap, w-halfw-gap, h-halfh-gap),
        ]
    return [
        (x, y, halfw, halfh),
        (x+halfw+gap, y, w-halfw-gap, halfh),
        (x, y+halfh+gap, halfw, h-halfh-gap),
        (x+halfw+gap, y+halfh+gap, w-halfw-gap, h-halfh-gap),
    ]


def _background(w: int, h: int) -> Image.Image:
    base = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(h-1, 1)
        strength = max(0.0, 1 - abs(t - 0.56) / 0.60)
        d.line([(0, y), (w, y)], fill=(
            int(7 + 5 * strength), int(29 + 21 * strength),
            int(71 + 52 * strength),
        ))
    motif = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = ImageDraw.Draw(motif)
    gap = 33
    m.rounded_rectangle((gap, gap, w-gap, h-gap), radius=22,
                        outline=(231, 181, 112, 145), width=2)
    for offset in (0, 26, 52):
        m.line([(w-330+offset, gap+28), (w-260+offset, gap+28),
                (w-210+offset, gap+78)], fill=(231, 181, 112, 42), width=2)
        m.ellipse((w-213+offset, gap+75, w-205+offset, gap+83),
                  fill=(231, 181, 112, 58))
    m.line((gap+28, h-gap-18, w-gap-28, h-gap-18),
           fill=(231, 181, 112, 30), width=2)
    return Image.alpha_composite(base.convert("RGBA"), motif).convert("RGB")


def _paste_photo(page: Image.Image, photo: bytes, rect: tuple[int, int, int, int]) -> None:
    x, y, width, height = rect
    d = ImageDraw.Draw(page)
    d.rounded_rectangle((x, y+5, x+width-1, y+height+3),
                        radius=17, fill="#04142D")
    d.rounded_rectangle((x, y, x+width-1, y+height-1),
                        radius=17, fill=GOLD)
    inset = 4
    interior = (width-inset*2, height-inset*2)
    with Image.open(BytesIO(photo)) as source:
        oriented = ImageOps.exif_transpose(source)
        fitted = ImageOps.fit(oriented.convert("RGB"), interior,
                              method=Image.Resampling.LANCZOS,
                              centering=(0.5, 0.45))
    mask = Image.new("L", interior)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, interior[0]-1, interior[1]-1), radius=13, fill=255,
    )
    page.paste(fitted, (x+inset, y+inset), mask)


def _draw_qr(page: Image.Image, url: str, x: int, y: int, size: int) -> None:
    panel = size + 34
    d = ImageDraw.Draw(page)
    d.rounded_rectangle((x, y, x+panel-1, y+panel-1), radius=15,
                        fill=QUIET, outline=GOLD, width=3)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=9, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color=NAVY, back_color=QUIET).convert("RGB")
    image = image.resize((size, size), Image.Resampling.NEAREST)
    page.paste(image, (x+17, y+17))


def _header(page: Image.Image, title: str, orientation: str, pad: int) -> int:
    w, _ = page.size
    d = ImageDraw.Draw(page)
    portrait = orientation == "portrait"
    header = 350 if portrait else 219
    d.text((pad+8, 82 if portrait else 61),
           "IMPERIAL COLLEGE LONDON  /  ELECTRICAL ENGINEERING SOCIETY",
           font=_font(18 if portrait else 21), fill=GOLD)
    with Image.open(LOGO) as source:
        pins = source.convert("RGBA")
        pins.thumbnail((370 if portrait else 390, 85), Image.Resampling.LANCZOS)
    if portrait:
        page.paste(pins, (w-pad-pins.width, 121), pins)
        event_y, font_size, maxwidth = 213, 62, w-pad*2-15
    else:
        page.paste(pins, (w-pad-pins.width, 109), pins)
        event_y, font_size, maxwidth = 109, 54, w-pad*2-pins.width-56
    label, font = _fit_title(d, title, maxwidth, font_size)
    d.text((pad+8, event_y), label, font=font, fill=IVORY)
    line_y = header-24 if portrait else header-17
    d.line((pad, line_y, w-pad, line_y), fill=GOLD, width=3)
    return header


def _footer(page: Image.Image, url: str, footer_top: int, pad: int,
            orientation: str) -> None:
    w, h = page.size
    d = ImageDraw.Draw(page)
    portrait = orientation == "portrait"
    d.line((pad, footer_top+22, w-pad, footer_top+22), fill=GOLD, width=3)
    qrsize = 198 if portrait else 170
    panel = qrsize+34
    _draw_qr(page, url, w-pad-panel, footer_top+35, qrsize)
    start = footer_top + (75 if portrait else 65)
    d.text((pad+8, start), "YOUR PHOTOS, TO KEEP.",
           font=_font(33 if portrait else 35, bold=True), fill=IVORY)
    d.text((pad+8, start+65), "SCAN THE CODE TO VIEW & DOWNLOAD",
           font=_font(22 if portrait else 26), fill=GOLD)
    d.text((pad+8, start+106), urlsplit(url).netloc,
           font=_font(21 if portrait else 24), fill=SOFT)


def render(
    photos: list[bytes], title: str, url: str,
    orientation: str = "portrait", print_label: str | None = None,
) -> bytes:
    """Compose the event title (or its configured short label) and QR into a print."""
    if not 1 <= len(photos) <= 4:
        raise ValueError("A print needs 1–4 photos")
    if orientation not in ("portrait", "landscape"):
        raise ValueError("Unknown print orientation")
    w, h = ((1200, 1800) if orientation == "portrait" else (1800, 1200))
    page = _background(w, h)
    pad = 67
    header = _header(page, print_label or title, orientation, pad)
    footer = 304 if orientation == "portrait" else 276
    photo_y = header + 5
    photo_bottom = h-footer
    for data, rect in zip(photos, layout(
        len(photos), (pad, photo_y, w-2*pad, photo_bottom-photo_y))):
        _paste_photo(page, data, rect)
    _footer(page, url, photo_bottom, pad, orientation)
    output = BytesIO()
    page.save(output, format="PNG", dpi=(300, 300), optimize=True)
    return output.getvalue()
