"""Isolated photo print tests; no Docker, DB or network required."""
import asyncio
import os
from io import BytesIO
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from PIL import Image

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
