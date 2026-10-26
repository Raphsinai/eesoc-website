"""High-resolution EESoc 6x4 collages with a scannable public-gallery QR.

Designed for the SELPHY CP1500: 1200 x 1800 or 1800 x 1200 at 300 DPI.
The output is a standard RGB PNG, compatible with the iPhone share sheet.
"""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode

BLUE = "#123A75"
INK = "#192841"
PALE = "#F4F7FB"
LOGO = Path(__file__).resolve().parent.parent / "assets" / "eesoc-mark.png"


@lru_cache(maxsize=24)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit_title(draw: ImageDraw.ImageDraw, title: str, maxwidth: int, size: int) -> tuple[str, ImageFont.ImageFont]:
    title = " ".join(title.split()) or "Your EESoc photos"
    font = _font(size, bold=True)
    while draw.textbbox((0, 0), title, font=font)[2] > maxwidth and size > 20:
        size -= 2
        font = _font(size, bold=True)
    if draw.textbbox((0, 0), title, font=font)[2] > maxwidth:
        while title and draw.textbbox((0, 0), title + "...", font=font)[2] > maxwidth:
            title = title[:-1]
        title += "..."
    return title, font


def layout(count: int, area: tuple[int, int, int, int], gap: int = 20) -> list[tuple[int, int, int, int]]:
    """Return x,y,w,h for 1–4 photos."""
    if count not in (1, 2, 3, 4):
        raise ValueError("A print needs 1–4 photos")
    x, y, w, h = area
    if count == 1:
        return [(x, y, w, h)]
    if count == 2:
        half = (h-gap)//2
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


def _paste_photo(base: Image.Image, photo: bytes, rect: tuple[int, int, int, int]) -> None:
    x, y, width, height = rect
    with Image.open(BytesIO(photo)) as source:
        oriented = ImageOps.exif_transpose(source)
        fitted = ImageOps.fit(
            oriented.convert("RGB"), (width, height),
            method=Image.Resampling.LANCZOS, centering=(0.5, 0.45),
        )
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width-1, height-1), radius=20, fill=255)
    base.paste(fitted, (x, y), mask)


def render(
    photos: list[bytes], title: str, url: str, orientation: str = "portrait",
) -> bytes:
    """Compose photos and QR; include 300 DPI metadata."""
    if not 1 <= len(photos) <= 4:
        raise ValueError("A print needs 1–4 photos")
    if orientation not in ("portrait", "landscape"):
        raise ValueError("Unknown print orientation")
    w, h = ((1200, 1800) if orientation == "portrait" else (1800, 1200))
    page = Image.new("RGB", (w, h), "#FFFFFF")
    d = ImageDraw.Draw(page)
    pad = 62
    header = 200 if orientation == "portrait" else 165
    footer = 300 if orientation == "portrait" else 270

    d.rounded_rectangle((pad, 55, w-pad, header-20), radius=22, fill=PALE)
    with Image.open(LOGO) as source:
        logo = source.convert("RGBA")
        logo.thumbnail((268, 98), Image.Resampling.LANCZOS)
    logo_x = pad+24
    logo_y = 74 if orientation == "portrait" else 66
    page.paste(logo, (logo_x, logo_y), logo)

    d.text((logo_x+280, 82 if orientation == "portrait" else 68),
           "EESOC PHOTO PRINT", font=_font(37, True), fill=BLUE)
    d.text((logo_x+280, 133 if orientation == "portrait" else 118),
           "IMPERIAL COLLEGE LONDON", font=_font(19), fill=INK)

    photo_y = header+2
    photo_bottom = h-footer
    for data, rect in zip(photos, layout(len(photos), (pad, photo_y, w-2*pad, photo_bottom-photo_y))):
        _paste_photo(page, data, rect)

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=9, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qrimage = qr.make_image(fill_color=BLUE, back_color="white").convert("RGB")
    qsize = 205 if orientation == "portrait" else 170
    qrimage = qrimage.resize((qsize, qsize), Image.Resampling.NEAREST)
    qr_x = w - pad - qsize
    qr_y = photo_bottom + (footer-qsize)//2
    page.paste(qrimage, (qr_x, qr_y))

    text_x = pad+8
    text_y = photo_bottom + (55 if orientation == "portrait" else 28)
    label, font = _fit_title(d, title, qr_x-text_x-38, 42 if orientation == "portrait" else 34)
    d.text((text_x, text_y), label, font=font, fill=INK)
    d.text((text_x, text_y+(64 if orientation == "portrait" else 51)),
           "SCAN TO DOWNLOAD YOUR PHOTOS", font=_font(23, True), fill=BLUE)
    domain = urlsplit(url).netloc
    d.text((text_x, text_y+(108 if orientation == "portrait" else 88)),
           f"{domain}/photos", font=_font(23), fill="#59677B")
    output = BytesIO()
    page.save(output, format="PNG", dpi=(300, 300), optimize=True)
    return output.getvalue()
