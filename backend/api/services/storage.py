"""File storage, with a local and an S3-compatible backend.

Uploads cannot live on the container filesystem: under Kubernetes the pod is
replaced on every deploy and anything written locally goes with it. The first
version of this used a ReadWriteMany PersistentVolume shared between the api
(writing) and nginx (reading), which works but drags in a dependency many
clusters do not have — an RWX StorageClass — and pins both pods to wherever that
volume can be mounted.

Object storage removes that. It also means a file is served straight from the
bucket or its CDN, so an image request never occupies a Python worker and never
touches the cluster at all.

Local disk is kept as the development backend, so `docker compose up` needs no
bucket and no credentials.

The four functions below are the whole interface: `save`, `delete`,
`public_url`, `describe`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
import secrets
from pathlib import Path

from fastapi import HTTPException, UploadFile

log = logging.getLogger("api.storage")

# --- shared policy ---------------------------------------------------------

MAX_BYTES = int(os.environ.get("MEDIA_MAX_BYTES", str(10 * 1024 * 1024)))

# Allow-list, not a block-list. SVG is deliberately absent: it can carry script
# and would be served from a trusted origin.
ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# --- image processing ------------------------------------------------------
#
# The old site served whatever was uploaded, and the 2023/24 handover reports the
# result: "extremely long loading times, especially on mobile", with a plea to
# hand-compress everything to tens of KB first. Nobody reliably does that, so it
# happens here instead.

# Longest edge. 1600px covers a full-width event banner on a 2x display; beyond
# that is detail no layout on the site can show.
IMAGE_MAX_EDGE = int(os.environ.get("MEDIA_MAX_EDGE", "1600"))
IMAGE_QUALITY = int(os.environ.get("MEDIA_WEBP_QUALITY", "82"))

# Re-encoded to WebP. GIF is excluded because it may be animated and Pillow would
# flatten it to a single frame; PDF is not an image at all.
RECODE_TO_WEBP = {"image/jpeg", "image/png", "image/webp"}

BACKEND = os.environ.get("MEDIA_BACKEND", "local").strip().lower()

# --- local backend ---------------------------------------------------------

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/data/media"))
MEDIA_URL_PREFIX = os.environ.get("MEDIA_URL_PREFIX", "/media")

# --- s3 backend ------------------------------------------------------------

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "") or None  # MinIO / R2 / Spaces
S3_REGION = os.environ.get("S3_REGION", "auto")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY_ID", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "media").strip("/")
# Where the browser fetches from: a CDN hostname, or the bucket's public URL.
S3_PUBLIC_BASE = os.environ.get("S3_PUBLIC_BASE_URL", "").rstrip("/")

_s3_client = None


def _s3():
    """Lazily build the client so the local backend never imports boto3."""
    global _s3_client
    if _s3_client is None:
        import boto3  # imported here on purpose: unused in local mode

        _s3_client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            region_name=S3_REGION,
            aws_access_key_id=S3_ACCESS_KEY or None,
            aws_secret_access_key=S3_SECRET_KEY or None,
        )
    return _s3_client


def using_s3() -> bool:
    return BACKEND == "s3"


# --- interface -------------------------------------------------------------


def ensure_root() -> None:
    """Prepare the backend. A no-op for S3 — the bucket is not ours to create."""
    if not using_s3():
        MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


def public_url(stored_name: str) -> str:
    """Browser-facing URL for a stored object.

    Absolute for S3 so the request bypasses the cluster entirely; site-relative
    for local, where nginx serves it off the shared volume.
    """
    if using_s3():
        if S3_PUBLIC_BASE:
            return f"{S3_PUBLIC_BASE}/{stored_name}"
        # No CDN configured: fall back to the bucket's own endpoint. Works, but
        # ties published URLs to the storage host — set S3_PUBLIC_BASE_URL.
        base = (S3_ENDPOINT or "https://s3.amazonaws.com").rstrip("/")
        return f"{base}/{S3_BUCKET}/{stored_name}"
    return f"{MEDIA_URL_PREFIX}/{stored_name}"


def _key(extension: str) -> str:
    """Random object name, sharded two characters deep.

    Random rather than user-supplied: an attacker-chosen filename is how you get
    path traversal and overwritten files. The shard keeps directory listings
    small on the local backend and spreads keys across prefixes on S3, which is
    what its partitioning likes.
    """
    name = f"{secrets.token_hex(16)}{extension}"
    return f"{name[:2]}/{name}"


async def _read_checked(upload: UploadFile) -> tuple[bytes, str, int]:
    """Read the upload, enforcing type and size before anything is written."""
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED:
        raise HTTPException(
            400,
            f"{content_type or 'that file type'} is not allowed. "
            f"Accepted: {', '.join(sorted(ALLOWED))}.",
        )

    # Chunked, so a huge upload cannot balloon memory before the size check.
    chunks: list[bytes] = []
    size = 0
    digest = hashlib.sha256()
    while chunk := await upload.read(1024 * 256):
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(
                413, f"Files are limited to {MAX_BYTES // (1024 * 1024)} MB."
            )
        digest.update(chunk)
        chunks.append(chunk)

    if size == 0:
        raise HTTPException(400, "That file is empty.")
    return b"".join(chunks), content_type, size


def _shrink(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale and re-encode an image. Returns the original on any failure.

    Runs in a worker thread via `save`. Falling back to the original is
    deliberate: a committee photo that is merely large is far better than an
    upload that 500s because Pillow disliked the file.
    """
    from io import BytesIO

    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(data)) as image:
            # Honour the EXIF orientation flag, or phone photos arrive rotated.
            image = ImageOps.exif_transpose(image)

            if max(image.size) > IMAGE_MAX_EDGE:
                image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)

            # Flatten transparency onto white: WebP keeps alpha, but a logo with
            # a transparent background over a dark card renders as a black box.
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                backdrop = Image.new("RGBA", image.size, (255, 255, 255, 255))
                image = Image.alpha_composite(backdrop, image)

            buffer = BytesIO()
            image.convert("RGB").save(
                buffer, format="WEBP", quality=IMAGE_QUALITY, method=6
            )
            shrunk = buffer.getvalue()

        # Only take the re-encode if it actually helped. A small, already
        # optimised PNG can come out bigger as WebP.
        if len(shrunk) < len(data):
            return shrunk, "image/webp"
        return data, content_type
    except Exception:  # noqa: BLE001
        log.warning("Could not re-encode a %s upload; storing the original", content_type)
        return data, content_type


async def save(upload: UploadFile, purpose: str = "general") -> dict:
    """Persist an uploaded file; returns the metadata row to record."""
    data, content_type, size = await _read_checked(upload)

    original = SAFE_NAME.sub("-", Path(upload.filename or "upload").name)[:120]

    if content_type in RECODE_TO_WEBP:
        # CPU-bound, so off the event loop.
        data, content_type = await asyncio.to_thread(_shrink, data, content_type)
        size = len(data)

    extension = ALLOWED.get(content_type) or mimetypes.guess_extension(content_type) or ""
    key = _key(extension)

    if using_s3():
        # Imported lazily like boto3 itself, so the local backend never needs
        # botocore present either.
        from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

        object_key = f"{S3_PREFIX}/{key}" if S3_PREFIX else key
        # Object storage runs as a single replica under a Recreate strategy, so
        # every deploy of it briefly removes the S3 endpoint entirely. That is a
        # retryable state, not a broken one, and surfacing it as a 500 would
        # tell the committee member mid-upload the exact opposite of the truth.
        # 503 says what is so: it will be back, try again in a moment.
        try:
            await asyncio.to_thread(
                _s3().put_object,
                Bucket=S3_BUCKET,
                Key=object_key,
                Body=data,
                ContentType=content_type,
                # Long cache: keys are random and never reused, so a stored object
                # is immutable.
                CacheControl="public, max-age=31536000, immutable",
            )
        except (ClientError, EndpointConnectionError, BotoCoreError) as exc:
            log.warning("Object store unreachable while saving an upload: %s", exc)
            raise HTTPException(
                503,
                "Uploads are temporarily unavailable while object storage "
                "restarts — please retry in a moment.",
            ) from exc
        stored = object_key
    else:
        ensure_root()
        destination = MEDIA_ROOT / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp name and rename: a crash mid-write must not leave a
        # half-written file being served.
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(destination)
        stored = key

    return {
        "stored_name": stored,
        "original_name": original,
        "content_type": content_type,
        "byte_size": size,
        "purpose": purpose,
    }


async def delete(stored_name: str) -> bool:
    """Remove an object. Missing counts as success — the goal is 'gone'."""
    if using_s3():
        try:
            await asyncio.to_thread(
                _s3().delete_object, Bucket=S3_BUCKET, Key=stored_name
            )
            return True
        except Exception:
            log.exception("Could not delete %s from bucket", stored_name)
            return False

    target = (MEDIA_ROOT / stored_name).resolve()
    root = MEDIA_ROOT.resolve()
    # A stored_name from the database must never escape the media root.
    if not str(target).startswith(str(root)):
        raise HTTPException(400, "Refusing to delete outside the media root")
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def describe() -> dict:
    """Backend summary for the admin dashboard and the readiness probe.

    Byte totals are NOT computed here. On S3 that would mean listing the whole
    bucket on every dashboard load; the `uploads` table already records
    `byte_size` per file, so the caller sums that instead.
    """
    if using_s3():
        return {
            "backend": "s3",
            "bucket": S3_BUCKET,
            "endpoint": S3_ENDPOINT or "aws",
            "public_base": S3_PUBLIC_BASE or None,
            "configured": bool(S3_BUCKET),
        }
    return {
        "backend": "local",
        "root": str(MEDIA_ROOT),
        "configured": MEDIA_ROOT.exists(),
    }


async def healthy() -> bool:
    """Can we reach the store? Used by the readiness probe.

    A HEAD on the bucket rather than a write: readiness is checked frequently and
    should not leave objects behind.
    """
    if using_s3():
        if not S3_BUCKET:
            return False
        try:
            await asyncio.to_thread(_s3().head_bucket, Bucket=S3_BUCKET)
            return True
        except Exception:
            log.warning("Bucket %s not reachable", S3_BUCKET)
            return False
    return MEDIA_ROOT.exists()
