"""Two api replicas never both run the union sync.

The real bridge rate-limits per source IP and a breach bans the whole society,
so the daily sync must run on exactly one pod even when the deployment has two
replicas racing to start. The design enforces that with a Postgres-level claim,
visible across pods; with it in place, exactly one replica's startup sync (and
one replica's daily sync) issues union requests.

This test measures the stub's request counts: one sync run is exactly three
GETs (/members, /products, /sales — today is not 1 August, so /committee is
not fetched), so a correct claim lands the total at 3 and a broken one at 6.

Gated on EESOC_ROLLOUT because it needs the live stack and drives `docker
compose scale`; the shared pytest job and local runs skip it.
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

# One sync run trips exactly these three stub GETs today.
SYNC_PATHS = ("/members", "/products", "/sales")
SETTLE_TIMEOUT = 120.0


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


def _sync_requests(counts: dict) -> int:
    """The number of sync-path requests in a /counts payload."""
    requests = counts.get("requests", {})
    return sum(requests.get(path, 0) for path in SYNC_PATHS)


def _wait_until_reset_counts_stable(union_stub) -> dict:
    """Poll /counts until one sync run has clearly happened and stayed that way.

    Waits for the three-path sum to reach 3 and then hold for three consecutive
    polls. A broken claim shows up as a jump to 6 (or beyond) and is returned
    immediately so the caller's exact-3 assertion can name it instead of timing
    out. Scaling up `may recreate` both replicas, each re-running its startup
    sync, so this also gives the losing pod time to wrongly sync if it will.
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT
    stable = 0
    last = None
    while time.monotonic() < deadline:
        counts = union_stub.get("/counts").json()
        last = counts
        value = _sync_requests(counts)
        if value > 3:
            return counts
        if value == 3:
            stable += 1
            if stable >= 3:
                return counts
        else:
            stable = 0
        time.sleep(1.0)
    raise AssertionError(
        f"startup sync never settled to one run within {SETTLE_TIMEOUT}s: {last}"
    )


def test_single_startup_sync_across_two_replicas(api, union_stub):
    """Exactly one replica's sync issues union requests after scaling to two.

    The stub-truth check runs before accounting matters: /health would itself
    count as a request, so it must not happen in the measured window. The scale
    back to one replica runs in `finally` so later suites in this job start
    from a single api again.
    """
    health = union_stub.get("/health")
    assert health.json().get("stub") is True, (
        "union service is not the stub — aborting rather than spending real quota"
    )

    reset = union_stub.post("/counts/reset")
    assert reset.status_code in (200, 201), reset.text

    try:
        _compose("up", "-d", "--scale", "api=2", "--no-deps", "api")

        # The nginx `api` name keeps routing to whichever replica answers;
        # wait until that proxy path is healthy before reading the stub counts.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if api.get("/health").status_code == 200:
                break
            time.sleep(1.0)
        else:
            raise AssertionError("api never became healthy after scaling to 2")

        counts = _wait_until_reset_counts_stable(union_stub)

        requests = counts.get("requests", {})
        for path in SYNC_PATHS:
            assert requests.get(path, 0) == 1, (
                f"{path} was requested {requests.get(path, 0)} times; "
                f"expected exactly one sync run: {requests}"
            )
        # Not 1 August, so the committee feed is not part of today's one run —
        # and its absence proves the run really was a single standard sync.
        assert requests.get("/committee", 0) == 0, requests
        assert _sync_requests(counts) == 3, counts
    finally:
        _compose("up", "-d", "--scale", "api=1", "--no-deps", "api")