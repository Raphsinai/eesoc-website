"""Fast, Docker-free checks for the photo booth layout and QR access rules."""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_ADAPTER_URL", "http://localhost:8081")
sys.path.insert(0, str(ROOT / "backend/api"))

from services import photo_print, storage
from routes import photos as api_photos

TEST_URL = "https://eesoc.raphsinai.dev/photos/cRYNsh_KeT2YiDthwQe3HdCQ6NmObS47"


@pytest.fixture
def jpeg_images():
    images = []
    for i in range(4):
        img = Image.new("RGB", (950, 700), ("red", "green", "blue", "gold")[i])
        out = io.BytesIO()
        img.save(out, format="JPEG")
        images.append(out.getvalue())
    return images


@pytest.mark.parametrize("orientation,dimensions", [
    ("portrait", (1181, 1747)), ("landscape", (1181, 1747)),
])
@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_print_is_exact_size_and_dpi(jpeg_images, orientation, dimensions, count):
    rendered = photo_print.render(jpeg_images[:count], "Engineering Society", TEST_URL, orientation)
    with Image.open(io.BytesIO(rendered)) as output:
        assert output.format == "PNG"
        assert output.mode == "RGB"
        assert output.size == dimensions
        assert round(output.info["dpi"][0]) == 300


@pytest.mark.parametrize("count", [0, 5])
def test_print_rejects_invalid_count(jpeg_images, count):
    with pytest.raises(ValueError, match="1–4"):
        photo_print.render(jpeg_images[:count] if count == 0 else jpeg_images + jpeg_images[:1], "Title", TEST_URL)


def test_normalise_is_full_resolution_and_strips_exif():
    img = Image.new("RGB", (2400, 1600), (13, 66, 190))
    exif = img.getexif()
    exif[271] = "TestCamera"
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    image_data, content_type = storage._normalise_photo(out.getvalue())
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(image_data)) as result:
        assert result.size == (2400, 1600)
        assert not result.getexif()


def test_photo_mode_rejects_gif():
    with pytest.raises(HTTPException) as error:
        storage._normalise_photo(b"not a photo")
    assert error.value.status_code == 400


def fake_row(status: str):
    return {
        "id": 31,
        "token": "cRYNsh_KeT2YiDthwQe3HdCQ6NmObS47",
        "title": "Engineering Society",
        "orientation": "portrait",
        "status": status,
        "created_by": 44,
        "created_at": "2026-10-26T12:00:00+00:00",
        "published_at": None,
        "images": [{
            "id": 51, "stored_name": "media/abc/photo.jpg",
            "original_name": "foo.jpg", "content_type": "image/jpeg",
        }],
    }


@pytest.mark.parametrize("status", ["draft", "published"])
def test_public_gallery_requires_published_status(monkeypatch, status):
    async def get(_path):
        return fake_row(status)

    monkeypatch.setattr(api_photos.database, "get", get)
    if status == "draft":
        with pytest.raises(HTTPException) as error:
            asyncio.run(api_photos.public_photo_set("cRYNsh_KeT2YiDthwQe3HdCQ6NmObS47"))
        assert error.value.status_code == 404
    else:
        result = asyncio.run(api_photos.public_photo_set("cRYNsh_KeT2YiDthwQe3HdCQ6NmObS47"))
        assert result["count"] == 1
        assert "/photos/" in result["url"]
        assert result["downloadUrl"].endswith("/download.zip")


def test_draft_has_no_public_link():
    draft = api_photos._present(fake_row("draft"))
    assert "url" not in draft
    assert "printUrl" not in draft
    assert "downloadUrl" not in draft


def test_database_adapter_jsonb_decoding():
    # The database adapter runs in a separate container. CI's test-host Python
    # intentionally lacks asyncpg, so execute only this pure helper function.
    import ast
    import json

    path = ROOT / "backend/database/routes/photos.py"
    tree = ast.parse(path.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_serialise"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"json": json}
    exec(compile(module, str(path), "exec"), namespace)
    result = namespace["_serialise"]({"id": 12, "images": '[{"id": 3}, {"id": 4}]'})
    assert [image["id"] for image in result["images"]] == [3, 4]
