"""Where to send somebody to actually pay.

A booking on this site is a hold; the money changes hands on the Union shop. So
every email about an unpaid ticket needs a link, and there are four places one
could come from. They are tried most-specific first:

  1. the ticket type's own `purchase_url`, set by the committee;
  2. the URL on the Union product that ticket type is linked to;
  3. the event's `purchase_url`, set by the committee;
  4. the URL on the Union product linked to the event as a whole.

The Union's feed often carries no URL at all for our centre, which is why (1)
and (3) exist. If none of them yields anything the caller gets None and the
email says "find it on the Union shop" — which is poor, but better than a link
to a page that does not exist.
"""

from __future__ import annotations

import logging

from clients import database
from utils.year import academic_year

log = logging.getLogger("api.purchase")


def _clean(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    # The feed sometimes carries a bare host. A link without a scheme is
    # resolved relative to the current page by most mail clients, which turns
    # it into a 404 on our own domain.
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url.lstrip('/')}"
    return url


async def _product_url(product_id: int, line_id: int | None, year: str) -> str | None:
    products = await database.get("/union/products", {"academic_year": year}) or []
    for product in products:
        if product.get("product_id") != product_id:
            continue
        if line_id is not None and product.get("product_line_id") != line_id:
            continue
        url = _clean(product.get("url"))
        if url:
            return url
    return derived_shop_url(product_id)


# The Union's feed often carries no URL for our centre's products, but the shop
# page follows the product id: eActivities product 1002713 is
# /shop/product/2713-/ on www-s (checked by hand for both Freshers' Barnight
# tickets, 2026-10-07; the bare www host 404s). Used only when nothing better —
# a link typed on the ticket or event, or a URL in the feed — exists.
SHOP_BASE = "https://www-s.imperialcollegeunion.org/shop/product"


def derived_shop_url(product_id: int | None) -> str | None:
    if not product_id or product_id <= 1_000_000:
        return None
    return f"{SHOP_BASE}/{product_id - 1_000_000}-/"


async def link_for(event: dict, ticket_type_id: int | None = None) -> str | None:
    """Resolve the buy-it link for one ticket type of one event.

    `event` is the adapter's event detail, which already carries its ticket
    types — so the per-type override costs no extra query.
    """
    year = academic_year()

    ticket = None
    if ticket_type_id is not None:
        ticket = next(
            (t for t in event.get("ticket_types", []) if t.get("id") == ticket_type_id),
            None,
        )

    if ticket:
        direct = _clean(ticket.get("purchase_url"))
        if direct:
            return direct

    links = await database.get("/product-links", {"event_id": event.get("id")}) or []

    if ticket_type_id is not None:
        for link in links:
            if link.get("ticket_type_id") == ticket_type_id:
                url = await _product_url(
                    link["union_product_id"], link.get("union_product_line_id"), year
                )
                if url:
                    return url

    direct = _clean(event.get("purchase_url"))
    if direct:
        return direct

    for link in links:
        if link.get("ticket_type_id") is None:
            url = await _product_url(
                link["union_product_id"], link.get("union_product_line_id"), year
            )
            if url:
                return url

    return None


async def links_for_event(event: dict) -> dict[int | None, str | None]:
    """Every ticket type's link in one pass, for an email that lists them all.

    Keyed by ticket type id, with None holding the event-wide fallback.
    """
    out: dict[int | None, str | None] = {None: await link_for(event, None)}
    for ticket in event.get("ticket_types", []):
        out[ticket["id"]] = await link_for(event, ticket["id"])
    return out
