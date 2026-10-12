"""WhatsApp posts, without the stack.

Pure unit tests: the database adapter is replaced by an in-memory fake, so they
run anywhere the api's requirements are installed. They pin what the WhatsApp
version of an announcement is for:

  1. the committee's markdown means the same as in the email — a heading, bold,
     bullets and links — written the way WhatsApp draws it;
  2. a post about an event carries that event's details and link from the
     database, as the email does, naming the event once;
  3. it warns before a post that would send people to a dead link or say
     tickets are on sale when they aren't;
  4. the wa.me link carries the post exactly, and nothing is sent anywhere.
"""

import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:9")
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from fastapi import HTTPException  # noqa: E402

database = importlib.import_module("clients.database")
settings = importlib.import_module("config").settings
whatsapp = importlib.import_module("services.whatsapp")

ORIGIN = settings.site_origin


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


EVENT = {
    "id": 7,
    "slug": "robot-wars",
    "title": "Robot Wars",
    "venue": "EEE 403",
    "published": True,
    "starts_at": "2099-03-01T18:00:00+00:00",
    "ends_at": "2099-03-01T20:00:00+00:00",
    "sales_open_at": "2099-02-01T12:00:00+00:00",
    "doors_close_at": None,
    "purchase_url": "https://shop.example.com/robot-wars",
    "ticket_types": [{"id": 1, "name": "Standard", "price_pence": 500}],
}


@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    """The two reads a post makes: the event, and its Union product links."""
    events = {7: dict(EVENT)}

    async def get(path, params=None):
        if path.startswith("/events/"):
            event = events.get(int(path.split("/")[2]))
            return dict(event) if event else None
        if path in ("/product-links", "/union/products"):
            return []
        raise AssertionError(f"unexpected GET {path}")

    async def refuse(*args, **kwargs):
        raise AssertionError("rendering a WhatsApp post must not write anything")

    monkeypatch.setattr(database, "get", get)
    for name in ("post", "put", "patch", "delete"):
        monkeypatch.setattr(database, name, refuse)
    return events


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------- 1. the markdown


def test_markdown_becomes_whatsapp_formatting():
    text = whatsapp.from_markdown(
        "## What's on\n\n"
        "Come to the **AGM** on Friday.\nRoom 403.\n\n"
        "- pizza\n* drinks\n\n"
        "Sign up [here](https://forms.example.com/agm) or [/events](/events)."
    )
    assert text == (
        "*What's on*\n\n"
        "Come to the *AGM* on Friday.\nRoom 403.\n\n"
        # "* " would read as the start of something bold; "- " is a list.
        "- pizza\n- drinks\n\n"
        f"Sign up here (https://forms.example.com/agm) or /events ({ORIGIN}/events)."
    )


def test_links_that_lead_nowhere_keep_only_their_label():
    assert whatsapp.from_markdown("[click](javascript:void)") == "click"
    assert whatsapp.from_markdown("[https://a.example](https://a.example)") == "https://a.example"
    # WhatsApp makes a bare address tappable; "mailto:" in front is noise.
    assert whatsapp.from_markdown("[us](mailto:eesoc@ic.ac.uk)") == "us (eesoc@ic.ac.uk)"


def test_a_heading_with_bold_inside_is_bold_once():
    # WhatsApp has no nested bold: "**a* b*" would show its stars.
    assert whatsapp.from_markdown("# The **big** night") == "*The big night*"


def test_a_post_with_no_event_is_heading_then_words():
    text = run(whatsapp.message("  Elections  open ", "Vote by **Friday**."))
    assert text == "*Elections open*\n\nVote by *Friday*."
    assert run(whatsapp.message("", "")) == ""


# ------------------------------------------------------- 2. about an event


def test_on_sale_post_has_the_standard_opening_and_both_links():
    text = run(whatsapp.message(
        "Tickets for Robot Wars are on sale", "", dict(EVENT), "on_sale"
    ))
    assert text.startswith(
        "*Tickets for Robot Wars are on sale*\n\n"
        "Tickets for *Robot Wars* are on sale now.\n\n"
    )
    # The title is in the heading already, so it is not repeated above the details.
    assert "\n*Robot Wars*\n" not in text
    assert "*When:* " in text
    assert "*Where:* EEE 403" in text
    assert "*Tickets:* Standard — £5" in text
    assert text.endswith(
        f"Reserve your place: {ORIGIN}/events/robot-wars\n"
        "Buy on the Union shop: https://shop.example.com/robot-wars"
    )


def test_event_post_names_the_event_when_the_words_do_not():
    event = {
        **EVENT,
        "doors_close_at": "2099-03-01T18:30:00+00:00",
        "ticket_types": [
            {"id": 1, "name": "Members", "price_pence": 500},
            {"id": 2, "name": "Guests", "price_pence": 800},
            {"id": 3, "name": "Early bird", "price_pence": 300, "sold_out": True},
        ],
    }
    text = run(whatsapp.message("Room change", "We've moved upstairs.", event))
    assert text.startswith("*Room change*\n\nWe've moved upstairs.\n\n*Robot Wars*\n*When:* ")
    assert "*Doors close:* 18:30" in text
    # Several tickets are a list; a sold-out one is not offered.
    assert "*Tickets:*\n- Members — £5" in text
    assert "- Guests — £8" in text
    assert "Early bird" not in text
    # Not an on-sale post: the event page, not the shop.
    assert text.endswith(f"Full details and booking: {ORIGIN}/events/robot-wars")
    assert "Union shop" not in text


# ---------------------------------------------------------------- 3. warnings


def test_warns_before_a_dead_link_or_an_early_on_sale_post():
    assert whatsapp.warning(None) is None
    assert whatsapp.warning(dict(EVENT)) is None
    assert "isn't published" in whatsapp.warning({**EVENT, "published": False})
    assert "already started" in whatsapp.warning(
        {**EVENT, "starts_at": _iso(timedelta(hours=-1))}
    )
    early = {**EVENT, "sales_open_at": _iso(timedelta(days=1))}
    assert "Sales don't open until" in whatsapp.warning(early, "on_sale")
    # Telling people a sale is coming is fine; only "on sale now" is early.
    assert whatsapp.warning(early, "event_announcement") is None


# ------------------------------------------------------------ 4. the link


def test_share_link_carries_the_post_exactly():
    text = "*Bold* & more\n\n- a #1\n- 50% off? https://eesoc.com/events/x?y=1"
    url = whatsapp.share_url(text)
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "wa.me", "/")
    assert parse_qs(parsed.query)["text"] == [text]


def test_route_renders_without_sending(monkeypatch):
    admin = importlib.import_module("routes.admin")
    res = run(admin.whatsapp_render(
        heading="Robot Wars", body="", event_id=7, kind="on_sale", _=None
    ))
    assert res["text"].startswith("*Robot Wars*\n\nTickets for *Robot Wars* are on sale now.")
    assert parse_qs(urlparse(res["url"]).query)["text"] == [res["text"]]
    # The sale is in 2099: "on sale now" would be early, and the screen says so.
    assert "Sales don't open until" in res["warning"]

    with pytest.raises(HTTPException) as missing:
        run(admin.whatsapp_render(heading="", body="", event_id=99, kind=None, _=None))
    assert missing.value.status_code == 404

    empty = run(admin.whatsapp_render(heading="", body="", event_id=None, kind=None, _=None))
    assert empty == {"text": "", "url": None, "warning": None}
