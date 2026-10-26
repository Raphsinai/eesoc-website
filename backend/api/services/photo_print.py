"""EESoc premium navy/gold 300 DPI 6x4 event prints using official pins logo."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

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
    # A compact, quiet frame: the QR retains its encoded quiet zone for scanning.
    inset = 12
    panel = size + 2 * inset
    d = ImageDraw.Draw(page)
    d.rounded_rectangle((x, y, x+panel-1, y+panel-1), radius=11,
                        fill=QUIET, outline=GOLD, width=2)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=9, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color=NAVY, back_color=QUIET).convert("RGB")
    image = image.resize((size, size), Image.Resampling.NEAREST)
    page.paste(image, (x+inset, y+inset))


def _header(page: Image.Image, title: str, orientation: str, pad: int) -> int:
    w, _ = page.size
    d = ImageDraw.Draw(page)
    portrait = orientation == "portrait"
    header = 220 if portrait else 197
    d.text((pad+8, 64 if portrait else 41),
           "IMPERIAL COLLEGE LONDON  /  ELECTRICAL ENGINEERING SOCIETY",
           font=_font(18 if portrait else 21), fill=GOLD)

    # On both print orientations the event title and pins sit on the same row.
    row_center = 147 if portrait else 125
    with Image.open(LOGO) as source:
        pins = source.convert("RGBA")
        pins.thumbnail((325 if portrait else 390, 86), Image.Resampling.LANCZOS)
    logo_x = w-pad-pins.width
    page.paste(pins, (logo_x, round(row_center - pins.height/2)), pins)

    title_x = pad+8
    label, font = _fit_title(d, title, logo_x-title_x-30,
                             58 if portrait else 56)
    bbox = d.textbbox((0, 0), label, font=font)
    # Align visible glyph centres, not Pillow's font ascender origin.
    title_y = round(row_center - (bbox[1]+bbox[3])/2)
    d.text((title_x, title_y), label, font=font, fill=IVORY)
    d.line((pad, header-12, w-pad, header-12), fill=GOLD, width=2)
    return header


def _footer(page: Image.Image, url: str, footer_top: int, pad: int,
            orientation: str) -> None:
    w, _ = page.size
    d = ImageDraw.Draw(page)
    portrait = orientation == "portrait"
    d.line((pad, footer_top+12, w-pad, footer_top+12), fill=GOLD, width=2)

    qrsize = 145 if portrait else 132
    panel = qrsize + 24
    qr_top = footer_top + (15 if portrait else 12)
    _draw_qr(page, url, w-pad-panel, qr_top, qrsize)

    # This is a discreet printed reference, not a second headline.
    caption = "your photos, online"
    font = _font(26 if portrait else 27)
    bbox = d.textbbox((0, 0), caption, font=font)
    caption_y = round(qr_top + panel/2 - (bbox[1]+bbox[3])/2)
    d.text((pad+8, caption_y), caption, font=font, fill=SOFT)


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
    footer = 220 if orientation == "portrait" else 205
    photo_y = header + 5
    photo_bottom = h-footer
    for data, rect in zip(photos, layout(
        len(photos), (pad, photo_y, w-2*pad, photo_bottom-photo_y))):
        _paste_photo(page, data, rect)
    _footer(page, url, photo_bottom, pad, orientation)
    output = BytesIO()
    page.save(output, format="PNG", dpi=(300, 300), optimize=True)
    return output.getvalue()
