"""Fixtures for the pytest suites.

Every section's tests live under tests/<slug>/ and share this file; nothing in
those suites should need its own conftest. The clients talk to the test stack
(docker-compose.test.yml), which must be up — see tests/README.md for the exact
commands.

Base URLs are overridable so a local run can point at a stack on other ports
without editing this file.
"""

import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest

# Repo root — the compose files live there, and `docker compose` must run from
# it so relative build contexts and env files resolve.
REPO_ROOT = Path(__file__).resolve().parent.parent

CODE_RE = re.compile(r"\b(\d{6})\b")


def _client(base: str, **kwargs) -> httpx.Client:
    """One place to build a client, so timeouts are uniform everywhere."""
    return httpx.Client(base_url=base, timeout=10.0, **kwargs)


# ------------------------------------------------------------------ services
#
# Session-scoped: plain clients hold no state, so there is nothing to leak
# between tests, and recreating them per test would just re-open sockets.


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    """The api with the dev-login bypass on, behind the frontend's /api proxy.

    This is the main entry point: same origin the browser uses, so cookies and
    headers behave exactly as they do for real users.
    """
    return _client(os.environ.get("EESOC_API_BASE", "http://localhost:8080/api"))


@pytest.fixture(scope="session")
def api_nologin() -> httpx.Client:
    """The second api instance, with the bypass off (port 8081).

    For the real code flow: /auth/dev-login 404s here, so tests can drive
    request-code -> verify-code through mailpit. It shares the database, so
    accounts made through `api` exist under this instance too.

    No `/api` on the base: this instance is published straight from its
    container (docker-compose.test.yml), not through nginx, and the api itself
    serves from the root — `/api` is nginx's prefix, stripped before it arrives.
    """
    return _client(os.environ.get("EESOC_NOLOGIN_BASE", "http://localhost:8081"))


@pytest.fixture(scope="session")
def mailpit() -> httpx.Client:
    """Mailpit's JSON API (the inbox the test stack's SMTP points at)."""
    return _client(os.environ.get("EESOC_MAILPIT_BASE", "http://localhost:8025"))


@pytest.fixture(scope="session")
def union_stub() -> httpx.Client:
    """The stub ICU bridge, published on the host for direct assertions."""
    return _client(os.environ.get("EESOC_UNION_STUB_BASE", "http://localhost:8082"))


# ------------------------------------------------------------------- mailpit


def _message_text(message: dict) -> str:
    """Every text a code could be hiding in, joined for one regex pass.

    The login-code email puts the code in the subject, the HTML body and the
    plain-text body; joining avoids caring which part a client rendered.
    """
    parts = []
    for key in ("Subject", "Text", "HTML", "Snippet"):
        value = message.get(key) or ""
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


@pytest.fixture(scope="session")
def login_code_from_mailpit(mailpit: httpx.Client):
    """Return a callable that reads the newest sign-in code mailed to an address.

    Polls rather than fetching once: the api dispatches login-code mail in the
    background after a 202, so the message routinely is not there the instant
    the request returns. Raises with what mailpit holds if nothing arrives
    within the timeout, so a failure names the address that never got mail.
    """

    def _login_code_from_mailpit(email: str, timeout: float = 30.0) -> str:
        # Mailpit lists newest first, and the default page is plenty for a
        # test stack where every message was sent by a test.
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            listing = mailpit.get("/api/v1/messages", params={"limit": 100}).json()
            for summary in listing.get("messages", []):
                recipients = summary.get("To") or []
                addresses = " ".join(
                    str(r.get("Address") or "") for r in recipients if isinstance(r, dict)
                )
                if email.lower() not in addresses.lower():
                    continue
                detail = mailpit.get(f"/api/v1/message/{summary['ID']}").json()
                match = CODE_RE.search(_message_text(detail))
                if match:
                    return match.group(1)
                last_error = f"latest message to {email} has no 6-digit code"
            if last_error is None:
                last_error = f"no message to {email} in mailpit yet"
            time.sleep(1.0)
        raise AssertionError(f"login code never arrived: {last_error}")

    return _login_code_from_mailpit


# -------------------------------------------------------------------- logins
#
# Function-scoped on purpose: a session carries CSRF state and an identity,
# and tests must not be able to change either for a later test.


def _client_with_session(api: httpx.Client, cookies: httpx.Cookies, csrf_token: str):
    """A client over `api`'s base URL carrying a session and its CSRF token.

    The cookies are sent as an explicit Cookie header rather than through
    httpx's cookie jar: the jar matches on domain, which gets fussy across the
    localhost/127.0.0.1 forms a test may use. The header always goes out.
    """
    session = cookies.get("session")
    csrf = cookies.get("csrf")
    return _client(
        str(api.base_url),
        headers={
            "Cookie": f"session={session}; csrf={csrf}",
            "X-CSRF-Token": csrf_token,
        },
    )


@pytest.fixture()
def member_client(api: httpx.Client) -> httpx.Client:
    """Signed in as member1@ic.ac.uk, a member of the stub's roster.

    Dev-login resolves membership by looking the address up in the members the
    startup sync pulled from the stub, so this polls until `is_member` is true
    rather than trusting that the sync beat the test run — a fresh stack can
    still be mid-sync when the first test starts. Each attempt mints a new
    session, and only the last one is used.
    """
    deadline = time.monotonic() + 30.0
    while True:
        response = api.post("/auth/dev-login", json={"email": "member1@ic.ac.uk"})
        response.raise_for_status()
        if response.json().get("is_member"):
            break
        if time.monotonic() >= deadline:
            raise AssertionError(
                "member1@ic.ac.uk did not resolve as a member within 30s — the "
                "startup sync from the union stub may not have run"
            )
        time.sleep(1.0)
    return _client_with_session(api, response.cookies, response.json()["csrf_token"])


@pytest.fixture()
def admin_client(api: httpx.Client) -> httpx.Client:
    """Signed in as test-admin@ic.ac.uk with make_admin, bypassing the code
    flow — most admin-surface tests care about the admin behaviour, not the
    login."""
    response = api.post(
        "/auth/dev-login", json={"email": "test-admin@ic.ac.uk", "make_admin": True}
    )
    response.raise_for_status()
    return _client_with_session(api, response.cookies, response.json()["csrf_token"])


@pytest.fixture()
def superuser_client(api: httpx.Client) -> httpx.Client:
    """Signed in as the standing superuser — SUPERUSER_EMAIL's default, which
    dev-login makes a superuser on every request.

    For what only a superuser may do: `admin_client` is an admin, one rung
    lower. Added for tests/lockers (free locker allowances are superuser only).
    """
    response = api.post("/auth/dev-login", json={"email": "ras124@ic.ac.uk"})
    response.raise_for_status()
    assert response.json().get("role") == "superuser", response.json()
    return _client_with_session(api, response.cookies, response.json()["csrf_token"])


# ------------------------------------------------------------------- compose


@pytest.fixture(scope="session")
def compose_exec():
    """Return a callable that runs a command inside a test-stack container.

    Backs assertions that need to observe state where it lives (the database,
    the api process) rather than through the API. Uses `-T` because CI runs
    with no TTY allocated, and the same project name (`-p eesoc-test`) the
    stack was brought up with, so it hits the right containers even when a
    dev stack is up under the default project name.
    """
    base = [
        "docker", "compose",
        "-f", "docker-compose.yml",
        "-f", "docker-compose.test.yml",
        "-p", "eesoc-test",
    ]

    def _compose_exec(service: str, *cmd: str) -> str:
        result = subprocess.run(
            [*base, "exec", "-T", service, *cmd],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"compose exec {service} {cmd} failed ({result.returncode}):\n"
                f"{result.stderr}"
            )
        return result.stdout

    return _compose_exec
