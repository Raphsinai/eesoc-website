"""Every event email announces the doors-close cutoff (acceptance criterion 4).

Unit tests: no FastAPI, no database, no test stack. The seven public email
functions in ``backend/api/utils/email.py`` each gain a ``doors_close: str |
None = None`` keyword. When it is set ("18:15" as the presenter already
formats it) both the HTML and the plain-text body must say "Doors close" with
that time; when it is absent, neither body mentions doors at all.

``email.py`` imports ``config`` which imports ``pydantic_settings``. That
dependency is not installed in every environment, so ``config`` is preloaded
(with ``DATABASE_ADAPTER_URL`` set) and ``email.py`` is exec'd by path; if
pydantic_settings is genuinely missing the whole module skips cleanly, exactly
like the ics/present unit tests skip when their module is absent.
"""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UTILS_DIR = REPO_ROOT / "backend" / "api" / "utils"
CONFIG_PATH = REPO_ROOT / "backend" / "api" / "config.py"
EMAIL_PATH = UTILS_DIR / "email.py"

if not EMAIL_PATH.is_file():
    pytest.skip(
        f"backend/api/utils/email.py does not exist yet at {EMAIL_PATH}",
        allow_module_level=True,
    )


# Registered under a name that is NOT "email": email.py and smtplib both
# import the *stdlib* package of that name, so shadowing sys.modules["email"]
# with our module breaks `from email import policy` and every SMTP import.
EMAIL_MODULE_NAME = "eesoc_backend_email_doors_close_test"


def _load_email_module():
    """Exec email.py by path, providing the ``config`` dependency first.

    ``config.Settings`` reads ``DATABASE_ADAPTER_URL`` from the environment and
    fails loudly without it, so it is set before config.py is exec'd.
    """
    if EMAIL_MODULE_NAME in sys.modules:
        return sys.modules[EMAIL_MODULE_NAME]

    os.environ.setdefault("DATABASE_ADAPTER_URL", "http://localhost:8080")

    if "config" not in sys.modules:
        config_spec = importlib.util.spec_from_file_location("config", CONFIG_PATH)
        config = importlib.util.module_from_spec(config_spec)
        sys.modules["config"] = config
        config_spec.loader.exec_module(config)

    email_spec = importlib.util.spec_from_file_location(EMAIL_MODULE_NAME, EMAIL_PATH)
    email = importlib.util.module_from_spec(email_spec)
    sys.modules[EMAIL_MODULE_NAME] = email
    email_spec.loader.exec_module(email)
    return email


# The seven functions under test, with the arguments each one requires.
FUNCTIONS = [
    (
        "send_booking_held",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            ticket_type="Standard",
            quantity=1,
            price="£5",
            starts="Thu 9 Oct",
            expires="in an hour",
            booking_url="https://eesoc.example.com/book",
        ),
    ),
    (
        "send_booking_confirmed",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            ticket_type="Standard",
            starts="Thu 9 Oct",
        ),
    ),
    (
        "send_last_chance",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            ticket_type="Standard",
            quantity=1,
            expires="in an hour",
            booking_url="https://eesoc.example.com/book",
        ),
    ),
    (
        "send_waitlist_offer",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            ticket_type="Standard",
            expires="in an hour",
            url="https://eesoc.example.com/claim",
        ),
    ),
    (
        "send_purchase_reminder",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            quantity=1,
            url="https://eesoc.example.com/buy",
        ),
    ),
    (
        "send_tickets_on_sale",
        dict(
            to="member@example.com",
            event_title="EESoc Talks",
            when="Thu 9 Oct",
            venue="EEE 403",
            event_url="https://eesoc.example.com/events/slug",
            unsubscribe_url="https://eesoc.example.com/unsub",
        ),
    ),
    (
        "send_event_announcement",
        dict(
            to="member@example.com",
            subject="New event from EESoc",
            event_title="EESoc Talks",
            when="Thu 9 Oct",
            venue="EEE 403",
            event_url="https://eesoc.example.com/events/slug",
            unsubscribe_url="https://eesoc.example.com/unsub",
        ),
    ),
]

DOORS_CLOSE = "18:15"


def _capture_send(email, coro):
    """Run ``coro`` with the module's ``send``/``send_many`` stubbed to record
    every (subject, body, html) that goes out. Returns the captured list."""
    captured = []
    saved_send = email.send
    saved_send_many = email.send_many

    async def fake_send(to, subject, body, html=None, **kwargs):
        captured.append((subject, body, html))
        return True

    async def fake_send_many(recipients, subject, body, html=None, **kwargs):
        captured.append((subject, body, html))
        return ["captured@example.com"]

    email.send = fake_send
    email.send_many = fake_send_many
    try:
        asyncio.run(coro)
    finally:
        email.send = saved_send
        email.send_many = saved_send_many
    return captured


@pytest.mark.parametrize("func_name, kwargs", FUNCTIONS, ids=[f[0] for f in FUNCTIONS])
def test_email_announces_doors_close_when_set(func_name, kwargs):
    email = _load_email_module()
    func = getattr(email, func_name)

    captured = _capture_send(email, func(doors_close=DOORS_CLOSE, **kwargs))
    assert captured, f"{func_name} sent no email"

    for subject, body, html in captured:
        assert "Doors close" in body, (func_name, body)
        assert DOORS_CLOSE in body, (func_name, body)
        assert "Doors close" in (html or ""), (func_name, html)
        assert DOORS_CLOSE in (html or ""), (func_name, html)


@pytest.mark.parametrize("func_name, kwargs", FUNCTIONS, ids=[f[0] for f in FUNCTIONS])
def test_email_omits_doors_close_when_unset(func_name, kwargs):
    email = _load_email_module()
    func = getattr(email, func_name)

    captured = _capture_send(email, func(**kwargs))
    assert captured, f"{func_name} sent no email"

    for subject, body, html in captured:
        assert "Doors close" not in body, (func_name, body)
        assert "Doors close" not in (html or ""), (func_name, html)