"""/api/events and /api/health keep serving while the union stub is stopped.

Events live in the api's own database, never in the union bridge, so stopping
the bridge must not take the calendar down — and health (the liveness probe)
must not depend on the bridge either. This is the "one dependency is down, the
site's own pages still answer" half of the zero-downtime story.

Gated on EESOC_ROLLOUT because it drives `docker compose stop`/`start` and
needs the live stack; the shared pytest job and local runs skip it.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("EESOC_ROLLOUT"),
    reason="needs the rolling-deploy test stack (CI sets EESOC_ROLLOUT); docker is unavailable locally",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_BASE = [
    "docker", "compose",
    "-f", "docker-compose.yml",
    "-f", "docker-compose.test.yml",
    "-p", "eesoc-test",
]


def _compose(*args: str) -> None:
    """Run a docker compose command from the repo root, raising on failure."""
    result = subprocess.run(
        [*COMPOSE_BASE, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"docker compose {' '.join(args)} failed ({result.returncode}):\n"
            f"{result.stderr}"
        )


def _wait_until_ok(get) -> None:
    """Poll a closure returning a response until it is 200, or fail loudly.

    A short poll rather than a single assert: the moment the stub stops is fine
    for the api (events are local), but the proxy can take a beat to settle.
    """
    deadline = time.monotonic() + 20.0
    last = None
    while time.monotonic() < deadline:
        response = get()
        last = response
        if response.status_code == 200:
            return
        time.sleep(1.0)
    raise AssertionError(f"endpoint never returned 200 within 20s: {last}")


def test_events_and_health_with_union_stub_stopped(api):
    """The calendar and liveness answer with the bridge down, then it restarts.

    `stub` is restarted in `finally` so any suite after this one still has the
    bridge its data depends on. No assertion depends on what the stub serves;
    only that the api's own endpoints stay up without it.
    """
    _compose("stop", "union-stub")
    try:
        _wait_until_ok(lambda: api.get("/events"))

        listing = api.get("/events")
        assert listing.status_code == 200, listing.text
        payload = listing.json()
        # The events list may be empty on a fresh stack (the union mirror does
        # not carry events), but the shape must still be there — that is what
        # "served from our own database" looks like without a bridge.
        assert isinstance(payload.get("events"), list), payload

        health = api.get("/health")
        assert health.status_code == 200, health.text
        assert health.json().get("ok") is True
    finally:
        _compose("up", "-d", "--no-deps", "union-stub")