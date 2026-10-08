"""A bulk send survives the api container being killed and recreated.

The zero-downtime design makes each bulk recipient a claimable row: pending ->
claimed -> sent/failed. A killed api leaves the campaign `status='sending'`, and
the restarted api resumes it without ever re-messaging somebody already marked
sent — otherwise a mid-rollout crash would double-send to the whole society.

This test needs the live compose stack with the rollout override layered in
(batch size 1, so N recipients take ~N seconds and the kill lands mid-send), so
it is gated on EESOC_ROLLOUT, which only the CI `resumable-send` job sets. The
shared pytest job and local runs skip every test here.
"""

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("EESOC_ROLLOUT"),
    reason="needs the rolling-deploy test stack (CI sets EESOC_ROLLOUT); docker is unavailable locally",
)

# Same project name and compose files as conftest's compose_exec, so these
# commands hit the exact containers the stack was brought up with even when a
# dev stack is running under the default project name.
#
# The rollout overlay is in this list too, not just in the CI bring-up: this
# test recreates the api container itself (`up -d --no-deps api` below), and a
# container recreated by an invocation without the overlay comes back with the
# production env — a resumed batch of 400 at 2s pacing, a lease TTL of 900s
# and a stale window of 600s — which blows this test's own 120s completion
# deadline before the campaign could resume. Every compose command this test
# makes has to layer the same overlay the stack was brought up with.
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_BASE = [
    "docker", "compose",
    "-f", "docker-compose.yml",
    "-f", "docker-compose.test.yml",
    "-f", "tests/zero-downtime/docker-compose.rollout.yml",
    "-p", "eesoc-test",
]

RECIPIENT_COUNT = 15
CAMPAIGN_TIMEOUT = 120.0


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


def _addresses() -> list[str]:
    """Fake, run-unique list addresses.

    example.com on purpose: nothing here must resolve to a real mailbox, and
    a per-run token means a re-run within a CI window cannot see stale rows.
    """
    token = uuid.uuid4().hex[:8]
    return [f"rollout-{token}-{i}@example.com" for i in range(RECIPIENT_COUNT)]


def _clear_and_seed_test_list(admin_client, addresses: list[str]) -> None:
    """Make the campaign target exactly our addresses and nothing else.

    The migration that ships the test list seeds one real-looking address, so
    the list is wiped first; then the assertion below knows `sent` equals the
    number of seeded addresses, not that plus a straggler.
    """
    existing = admin_client.get("/email/test-recipients").json().get("recipients", [])
    for email in existing:
        admin_client.delete("/email/test-recipients", params={"email": email})
    for email in addresses:
        response = admin_client.post("/email/test-recipients", json={"email": email})
        response.raise_for_status()


def _message_recipient_counts(mailpit, addresses: set[str]) -> dict[str, int]:
    """Count, per target address, how many messages list it as a recipient.

    Bulk mail is BCC'd, so both the To and Bcc fields are read — a message
    could legitimately carry the recipient in either, and a duplicate is a
    duplicate whichever field it hides in. The envelope sender and any other
    bystander addresses are ignored because only our seeded set is counted.
    """
    counts = {address: 0 for address in addresses}
    listing = mailpit.get("/api/v1/messages", params={"limit": 500}).json()
    for summary in listing.get("messages", []):
        detail = mailpit.get(f"/api/v1/message/{summary['ID']}").json()
        seen = set()
        for field in ("To", "Bcc"):
            for entry in detail.get(field) or []:
                if not isinstance(entry, dict):
                    continue
                address = (entry.get("Address") or "").strip().lower()
                if address in addresses:
                    seen.add(address)
        for address in seen:
            counts[address] += 1
    return counts


def _campaign(admin_client, campaign_id: int) -> dict:
    """The campaign row for `campaign_id`, or an error if it vanished."""
    campaigns = admin_client.get("/email/campaigns", params={"limit": 200}).json()
    for row in campaigns:
        if row.get("id") == campaign_id:
            return row
    raise AssertionError(f"campaign {campaign_id} not found in /email/campaigns")


def _wait_for_running_send(admin_client, mailpit, addresses: set[str]) -> None:
    """Poll until at least two recipient messages exist, proving the send runs.

    With batch size 1, every recipient is its own message, so two messages
    means the first batches are out and ~13 recipients are still queued — the
    window where killing the api actually interrupts a send.
    """
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        counts = _message_recipient_counts(mailpit, addresses)
        if sum(counts.values()) >= 2:
            return
        time.sleep(0.5)
    raise AssertionError("campaign never reached 2 messages in mailpit — send not running")


def _wait_for_completion(admin_client, campaign_id: int) -> dict:
    """Poll the campaign until it is `sent`, or fail with what it got stuck as."""
    deadline = time.monotonic() + CAMPAIGN_TIMEOUT
    last = None
    while time.monotonic() < deadline:
        row = _campaign(admin_client, campaign_id)
        last = row
        if row.get("status") == "sent":
            return row
        if row.get("status") == "failed":
            raise AssertionError(f"campaign failed instead of resuming: {row}")
        time.sleep(1.0)
    raise AssertionError(f"campaign did not reach 'sent' within {CAMPAIGN_TIMEOUT}s: {last}")


def test_killed_api_resumes_campaign_without_duplicates(admin_client, mailpit):
    """Kill the api mid-send; the restarted api finishes with no double sends.

    SIGKILL is the point: no graceful handler runs, so any recipient marked
    sent before the kill must be the whole story — the resume logic, not a tidy
    shutdown, is what prevents re-sending.
    """
    addresses = _addresses()
    target = {a.lower() for a in addresses}
    _clear_and_seed_test_list(admin_client, addresses)

    sent = admin_client.post(
        "/email/send",
        json={
            "subject": f"Rollout test {uuid.uuid4().hex[:6]}",
            "body": "Resume-under-kill test body.",
            "audiences": ["test"],
        },
    )
    assert sent.status_code in (200, 201), sent.text
    campaign_id = sent.json()["campaignId"]

    try:
        _wait_for_running_send(admin_client, mailpit, target)

        # SIGKILL, then recreate from the same image: this is the deploy-time
        # "old container dies, new one starts" the plan has to survive.
        _compose("kill", "api")
        _compose("up", "-d", "--no-deps", "api")

        campaign = _wait_for_completion(admin_client, campaign_id)

        counts = _message_recipient_counts(mailpit, target)
        for address in target:
            assert counts[address] == 1, (
                f"{address} was delivered {counts[address]} times; expected exactly once"
            )
        assert campaign["sent"] == RECIPIENT_COUNT, campaign
        assert campaign["failed"] == 0, campaign
    finally:
        # Best effort: leave the test list as we found it in case other suites
        # in this job still want to send to it.
        for address in addresses:
            try:
                admin_client.delete("/email/test-recipients", params={"email": address})
            except Exception:  # noqa: BLE001
                pass