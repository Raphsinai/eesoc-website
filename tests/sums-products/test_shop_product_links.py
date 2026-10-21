"""SUMS shop ids must resolve to sales-feed ids even before product sync.

No real Union requests or Docker are needed for these unit tests.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:9")
API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from routes import admin  # noqa: E402
from services import purchase  # noqa: E402
from utils.shop_products import parse_shop_product_ref, resolve_product_id  # noqa: E402
from utils import deps  # noqa: E402
from models.auth import AuthSession  # noqa: E402

SHOP = "https://www-s.imperialcollegeunion.org/shop/product"
HALLOWEEN = (
    (7, "Early Bird - Alcoholic", "3023", 1003023),
    (8, "Early Bird - Non-Alcoholic", "3043", 1003043),
    (9, "General - Alcoholic", "3049", 1003049),
    (10, "General - Non-Alcoholic", "3047", 1003047),
)


@pytest.mark.parametrize("ticket_id,name,short_id,api_id", HALLOWEEN)
def test_halloween_sums_shop_id_correlates_with_sales_product_id(ticket_id, name, short_id, api_id):
    assert parse_shop_product_ref(short_id) == api_id
    assert parse_shop_product_ref(f"{SHOP}/{short_id}-/") == api_id
    assert parse_shop_product_ref(str(api_id)) == api_id
    assert purchase.derived_shop_url(api_id) == f"{SHOP}/{short_id}-/"


@pytest.mark.parametrize("invalid", (
    "", "  ", "0", "1000000", "-3023", "30.23", "abc", "shop/product/3023-/",
    "http://www-s.imperialcollegeunion.org/shop/product/3023-/",
    "https://evil.example/shop/product/3023-/",
    "https://www-s.imperialcollegeunion.org/shop/product/3023-/other",
    "https://www-s.imperialcollegeunion.org/shop/product/3023-/?other=1",
))
def test_malformed_shop_ids_are_rejected(invalid):
    with pytest.raises(ValueError):
        parse_shop_product_ref(invalid)


def test_existing_api_product_ids_are_left_unmodified():
    assert resolve_product_id(502, None) == 502
    assert resolve_product_id(1002713, None) == 1002713
    assert resolve_product_id(None, None) is None   # existing unlink behaviour
    assert resolve_product_id(None, "3023") == 1003023
    with pytest.raises(HTTPException) as exc:
        resolve_product_id(502, "3023")
    assert exc.value.status_code == 400


class FakeProductsDB:
    def __init__(self, links=()):
        self.links = [dict(link) for link in links]
        self.deleted = []
        self.inserted = []

    async def get(self, path, params=None):
        if path == "/product-links":
            return self.links
        if path == "/union/products":
            return []  # the product has NEVER appeared in the upstream feed
        raise AssertionError(f"unexpected GET {path}")

    async def delete(self, path):
        self.deleted.append(path)
        self.links = [link for link in self.links if path != f"/product-links/{link['id']}"]

    async def post(self, path, body, params=None):
        assert path == "/product-links"
        self.inserted.append(body)
        return {"id": len(self.inserted) + 100, **body}


def _install_ticket_stubs(monkeypatch, db):
    async def editable_ticket(ticket_id, session):
        return {"id": ticket_id, "event_id": 11}
    monkeypatch.setattr(admin, "_editable_ticket", editable_ticket)
    monkeypatch.setattr(admin, "database", db)


def _session():
    return SimpleNamespace(user=SimpleNamespace(id=23))


@pytest.mark.parametrize("ticket_id,name,short_id,api_id", HALLOWEEN)
def test_manual_halloween_ticket_link_works_without_synced_product(monkeypatch, ticket_id, name, short_id, api_id):
    db = FakeProductsDB()
    _install_ticket_stubs(monkeypatch, db)
    result = asyncio.run(admin.set_ticket_product(
        ticket_type_id=ticket_id, union_product_id=None, union_product_line_id=None,
        shop_product_ref=f"{SHOP}/{short_id}-/", session=_session(),
    ))
    assert result["linked"]["union_product_id"] == api_id
    assert result["linked"]["union_product_line_id"] is None
    assert db.inserted[0]["ticket_type_id"] == ticket_id
    assert db.inserted[0]["event_id"] == 11
    assert db.inserted[0]["created_by"] == 23


def test_invalid_manual_id_never_removes_existing_ticket_mapping(monkeypatch):
    old = {"id": 80, "ticket_type_id": 7, "event_id": 11, "union_product_id": 1003023}
    db = FakeProductsDB((old,))
    _install_ticket_stubs(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.set_ticket_product(
            ticket_type_id=7, union_product_id=None, union_product_line_id=None,
            shop_product_ref="bad reference", session=_session(),
        ))
    assert exc.value.status_code == 400
    assert db.deleted == []
    assert db.links == [old]
    assert db.inserted == []


def test_manual_event_and_non_event_links_do_not_need_product_feed(monkeypatch):
    db = FakeProductsDB()
    async def editable_event(event_id, session):
        return {"id": event_id}
    monkeypatch.setattr(admin, "_editable_event", editable_event)
    monkeypatch.setattr(admin, "database", db)
    result = asyncio.run(admin.set_event_product(
        event_id=11, union_product_id=None, shop_product_ref="3049", session=_session(),
    ))
    assert result["linked"]["union_product_id"] == 1003049
    assert result["linked"]["ticket_type_id"] is None

    body = {"purpose": "locker", "shop_product_ref": "2771", "event_id": None, "ticket_type_id": None}
    result = asyncio.run(admin.create_link(body, _session()))
    assert result["union_product_id"] == 1002771
    assert "shop_product_ref" not in db.inserted[-1]


def test_purchase_link_works_before_sync_without_override(monkeypatch):
    db = FakeProductsDB(({ "id": 101, "event_id": 11, "ticket_type_id": 7,
                           "union_product_id": 1003023, "union_product_line_id": None},))
    monkeypatch.setattr(purchase, "database", db)
    event = {"id": 11, "ticket_types": [{"id": 7, "purchase_url": None}], "purchase_url": None}
    assert asyncio.run(purchase.link_for(event, 7)) == f"{SHOP}/3023-/"


def test_permission_for_link_action_is_independent():
    from datetime import datetime, timezone
    def session(grants):
        return AuthSession(
            session={"token_hash": "abc", "csrf_token": "abc", "user_id": 3,
                     "expires_at": datetime.now(timezone.utc)},
            user={"id": 3, "email": "test@example.org"},
            role="events", permissions=grants,
        )
    denied = session(["events.view", "events.scope.all"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.require_action("events.ticket_types.product")(denied))
    assert exc.value.status_code == 403
    allowed = session(["events.ticket_types.product", "events.scope.all"])
    assert asyncio.run(deps.require_action("events.ticket_types.product")(allowed)) is allowed
