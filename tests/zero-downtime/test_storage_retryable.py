"""Uploads fail retryably, not with a 500, when object storage is unreachable.

A committee member uploading a poster during an S3 outage would otherwise see
a bare 500 and assume their file vanished or the site is broken. The
zero-downtime design pins `storage.save` on the S3 backend to raise
HTTPException(503) whose body says uploads are temporarily unavailable and to
retry. These are pure unit tests: the object-store endpoint points at
127.0.0.1:9 (the discard port, where nothing listens), so no stack, no docker
and no real store are touched.

`services.storage` reads `MEDIA_BACKEND` and the S3 settings at import time,
so every test sets the environment first and then imports (or reloads) the
module to see it.
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

# storage.py sits in backend/api/services/, imported as a namespace package
# (there is no __init__.py in services/), so the api directory must be on
# sys.path for `import services.storage` to resolve.
API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"

# Exactly the keys this file writes, so the autouse fixture restores only these
# and leaves every other environment variable alone.
MANAGED_ENV = (
    "MEDIA_BACKEND",
    "S3_ENDPOINT_URL",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_PUBLIC_BASE_URL",
    "S3_REGION",
    "S3_PREFIX",
    # botocore honours these to trim its retry budget; the refusal is instant
    # anyway, but one attempt keeps the failure we want (unreachable) fast.
    "AWS_MAX_ATTEMPTS",
    "AWS_RETRY_MODE",
)


@pytest.fixture(autouse=True)
def _restore_env():
    """Put every managed key back after a test, so s3 mode never leaks out."""
    saved = {key: os.environ.get(key) for key in MANAGED_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _load_storage():
    """Import services.storage with the current environment applied.

    Reloads if a sibling test already imported it, because the module reads
    BACKEND and the S3 settings once at import time and reload is how a test
    sees environment changes made after that first import.
    """
    sys.path.insert(0, str(API_DIR))
    name = "services.storage"
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _set_s3_env() -> None:
    os.environ.update(
        {
            "MEDIA_BACKEND": "s3",
            # Port 9 is the discard service: nothing listens, so the connection
            # is refused and the object store is unreachable by construction.
            "S3_ENDPOINT_URL": "http://127.0.0.1:9",
            "S3_BUCKET": "rollout-test-bucket",
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
            "S3_PUBLIC_BASE_URL": "",
            "AWS_MAX_ATTEMPTS": "1",
            "AWS_RETRY_MODE": "standard",
        }
    )


def _set_local_env() -> None:
    os.environ["MEDIA_BACKEND"] = "local"


class _PdfUpload:
    """A stand-in for FastAPI's UploadFile, just the parts storage.py reads.

    PDF rather than an image on purpose: `_read_checked` and `save` only need a
    known content type and a non-empty body, and application/pdf skips the
    Pillow re-encode entirely, so this unit test has no image bytes to get
    wrong and still reaches the object-store write.
    """

    content_type = "application/pdf"
    filename = "poster.pdf"

    def __init__(self, data: bytes):
        self._data = data
        self._eof = False

    async def read(self, size: int = -1) -> bytes:
        # One chunk then EOF, the way a real upload's BytesIO drains.
        if self._eof:
            return b""
        self._eof = True
        return self._data


def test_s3_save_failure_is_503_and_retryable():
    """A failed object-store write surfaces as HTTPException 503 with a retry hint.

    boto3 is imported lazily behind the S3 branch, so the importorskip only
    fires on a partial venv; it is a runtime dependency and real installs
    always have it. The 503 contract has landed, so anything else `save`
    raises here — a bare botocore error included — is a broken contract and
    must propagate as a test failure, not a skip: it is exactly what would
    otherwise reach the committee member as a 500.
    """
    pytest.importorskip("boto3", reason="the S3 backend imports boto3 lazily")
    _set_s3_env()
    storage = _load_storage()
    assert storage.using_s3() is True

    upload = _PdfUpload(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")

    try:
        asyncio.run(storage.save(upload))
    except HTTPException as exc:
        assert exc.status_code == 503, (
            f"expected 503, got {exc.status_code}: {exc.detail!r}"
        )
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        assert "retry" in detail.lower(), detail
    else:
        pytest.fail(
            "save() returned success against an unreachable endpoint; expected "
            "HTTPException 503"
        )


def test_local_backend_unchanged():
    """The local backend's mode and public URLs are not disturbed by the s3 work.

    This is the "did we break dev mode" guard: `docker compose up` runs with
    MEDIA_BACKEND unset, and its site-relative /media/... URLs must be exactly
    what nginx serves. It needs no bucket, so it never touches boto3.
    """
    _set_local_env()
    storage = _load_storage()
    assert storage.using_s3() is False
    assert storage.public_url("ab/cd.png") == "/media/ab/cd.png"