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
@pytest.mark.parametrize("orientation", ("portrait", "landscape"))
def test_print_dimensions_qr_and_safe_margins(image_bytes, count, orientation):
    from services import booth_template
    url = "https://eesoc.com/api/p/QrcodeTest123"
    result = photo_print.render([image_bytes] * count, "EESoc event print", url, orientation)
    with Image.open(BytesIO(result)) as image:
        assert image.format == "PNG"
        # The handoff specifies portrait postcards for every layout.
        assert image.size == (1181, 1747)
        assert image.getpixel((3,200)) == (11,13,18)
        assert image.getpixel((23,12)) == (231,181,112)
        assert image.getpixel((970,1495)) == (255,255,255)
        assert image.info["dpi"][0] == pytest.approx(300, abs=1)
        assert len(booth_template.spec()['layouts'][str(count)]['windows']) == count


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
        emoji_row = (0, 1480, image.width, 1580)
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
