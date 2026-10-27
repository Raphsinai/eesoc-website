"""Isolated photo print tests; no Docker, DB or network required."""
import asyncio
import os
from io import BytesIO
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "api"))

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:8009")
from services import photo_print, storage
from routes import photos


@pytest.fixture()
def image_bytes():
    image = Image.new("RGB", (1200, 850), (68, 117, 165))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


@pytest.mark.parametrize("count", range(1, 5))
@pytest.mark.parametrize("orientation,size", [
    ("portrait", (1200, 1800)),
    ("landscape", (1800, 1200)),
])
def test_print_dimensions_qr_and_safe_margins(image_bytes, count, orientation, size):
    url = "https://eesoc.raphsinai.dev/photos/Tk6uLhQRCourse2026PrintTesting"
    result = photo_print.render([image_bytes] * count, "EESoc event print", url, orientation)
    with Image.open(BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == size
        # The new design uses EESoc navy with gold lines: white only inside the QR.
        for x,y in ((0, 0), (size[0]-1, size[1]-1)):
            r,g,b = image.getpixel((x,y))
            assert b > g > r and b < 150
        assert image.info["dpi"][0] == pytest.approx(300, abs=1)
        assert image.getpixel((100, 450)) != (255, 255, 255)


@pytest.mark.parametrize("orientation,size,row_center", [
    ("portrait", (1200, 1800), 147),
    ("landscape", (1800, 1200), 125),
])
def test_print_has_aligned_header_and_discreet_footer(
    monkeypatch, image_bytes, orientation, size, row_center,
):
    drawn_text = []
    qr_positions = []
    original_text = ImageDraw.ImageDraw.text
    original_qr = photo_print._draw_qr

    def record_text(draw, xy, text, *args, **kwargs):
        drawn_text.append((text, xy, kwargs.get("font")))
        return original_text(draw, xy, text, *args, **kwargs)

    def record_qr(page, url, x, y, qr_size):
        qr_positions.append((x, y, qr_size))
        return original_qr(page, url, x, y, qr_size)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(photo_print, "_draw_qr", record_qr)
    photo_print.render([image_bytes], "Full event name", "https://eesoc.com/photos/sample",
                       orientation, print_label="Short event")

    assert len(qr_positions) == 1
    qr_x, qr_y, qr_size = qr_positions[0]
    assert photo_print.QR_FRAME_PADDING == 5
    qr_panel = qr_size + 2 * photo_print.QR_FRAME_PADDING
    assert qr_size <= 145
    assert qr_x + qr_panel == size[0] - 67
    assert qr_y + qr_panel < size[1] - 33

    title, = [entry for entry in drawn_text if entry[0] == "Short event"]
    caption, = [entry for entry in drawn_text if entry[0] == "your photos, online"]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def text_center(entry):
        content, xy, font = entry
        bbox = probe.textbbox((0, 0), content, font=font)
        return xy[1] + (bbox[1] + bbox[3]) / 2

    assert abs(text_center(title) - row_center) <= 1
    assert abs(text_center(caption) - (qr_y + qr_panel / 2)) <= 1
    assert all(entry[0] != "YOUR PHOTOS, TO KEEP." for entry in drawn_text)


@pytest.mark.parametrize("emoji", ["🎃", "❤️", "🇬🇧", "👩🏽‍🎓", "1️⃣"])
def test_emoji_glyphs_render_in_colour(emoji):
    tile = photo_print._emoji_tile(emoji, 56)
    assert tile.mode == "RGBA"
    assert tile.getbbox() is not None
    assert 20 <= tile.width <= 85
    assert tile.height == round(56 * 0.88)
    # A missing-glyph rectangle from a plain text font is monochrome;
    # the bundled Noto glyph has real colour pixels.
    assert any(
        max(r, g, b) - min(r, g, b) > 40
        for r, g, b, a in tile.getdata() if a > 180
    )


@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_event_print_preserves_emoji_in_short_label(image_bytes, orientation):
    url = "https://eesoc.com/photos/emoji-test"
    result = photo_print.render(
        [image_bytes], "Full event name", url, orientation,
        print_label="Graduation 👩🏽‍🎓 🇬🇧",
    )
    plain = photo_print.render(
        [image_bytes], "Full event name", url, orientation,
        print_label="Graduation ",
    )
    with Image.open(BytesIO(result)) as image, Image.open(BytesIO(plain)) as baseline:
        assert image.format == "PNG"
        # The text and background remain the same; the colour glyphs add pixels
        # to the title line even after full print composition.
        emoji_row = (0, 90, image.width, 185)
        assert ImageChops.difference(
            image.crop(emoji_row), baseline.crop(emoji_row),
        ).getbbox() is not None


def test_emoji_title_truncation_never_splits_grapheme():
    import regex

    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    sequence = "👩🏽‍🎓"
    title = (sequence + " ") * 40
    fitted, font = photo_print._fit_title(draw, title, 290, 58)
    assert fitted.endswith("...")
    assert photo_print._title_width(draw, fitted, font) <= 290
    assert all(
        grapheme.isspace() or grapheme == sequence
        for grapheme in regex.findall(r"\X", fitted[:-3])
    )


def test_unicode_titles_without_emoji_still_fit():
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    fitted, font = photo_print._fit_title(draw, "Électricité — 2026", 650, 58)
    assert fitted == "Électricité — 2026"
    assert photo_print._title_width(draw, fitted, font) <= 650


@pytest.mark.parametrize("count", range(1, 5))
def test_layout_stays_in_bounds_and_tiles_do_not_overlap(count):
    tiles = photo_print.layout(count, (35, 80, 850, 1120))
    assert len(tiles) == count
    for i, (x, y, w, h) in enumerate(tiles):
        assert x >= 35 and y >= 80 and w > 0 and h > 0
        assert x + w <= 885 and y + h <= 1200
        for other in tiles[i+1:]:
            ax, ay, aw, ah = other
            assert x + w <= ax or ax + aw <= x or y + h <= ay or ay + ah <= y


def test_upload_photo_preserves_resolution_strips_exif_and_corrects_orientation(image_bytes):
    src = Image.new("RGB", (900, 600), "#2088af")
    exif = Image.Exif()
    exif[274] = 6  # Sony and other cameras use this for portrait orientation.
    raw = BytesIO()
    src.save(raw, format="JPEG", exif=exif)
    result, content_type = storage._normalise_photo(raw.getvalue())
    with Image.open(BytesIO(result)) as image:
        assert content_type == "image/jpeg"
        assert image.size == (600, 900)
        assert not image.getexif()


def test_invalid_images_rejected(image_bytes):
    with pytest.raises(HTTPException) as err:
        storage._normalise_photo(b"not-an-image")
    assert err.value.status_code == 400

    small = BytesIO()
    Image.new("RGB", (320, 240), "#eeeeee").save(small, format="PNG")
    with pytest.raises(HTTPException) as err:
        storage._normalise_photo(small.getvalue())
    assert err.value.status_code == 400


def test_draft_is_never_exposed_by_public_token(monkeypatch):
    async def fake_get(path):
        return {"id": 123, "token": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "status": "draft"}
    monkeypatch.setattr(photos.database, "get", fake_get)
    for endpoint in (photos.public_photo_set, photos.public_print, photos.download_photos):
        with pytest.raises(HTTPException) as err:
            asyncio.run(endpoint("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
        assert err.value.status_code == 404
