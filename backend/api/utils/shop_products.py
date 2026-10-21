"""Convert manually entered Imperial College Union shop references to eActivities ids.

Our eActivities reports refer to product 1002713 while the Union shop links to
/shop/product/2713-/.  Do not rewrite ids from the cached eActivities feed;
this conversion is *only* for the explicit manual shop-reference field.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import HTTPException

SHOP_ID_OFFSET = 1_000_000
SHOP_HOST = "www-s.imperialcollegeunion.org"
SHOP_PATH = re.compile(r"/shop/product/([0-9]+)-/?\Z")
NUMBER = re.compile(r"[0-9]+\Z")


def parse_shop_product_ref(value: str) -> int:
    """Accept a short SUMS/shop id, full shop URL, or eActivities product id.

    The short number 3023 becomes 1003023 (the id in sales/product feeds).
    A full eActivities id is used unchanged. Unknown hosts/paths, empty,
    fractional, or zero ids are errors, not silent unlinks.
    """
    raw = value.strip()
    if NUMBER.fullmatch(raw):
        number = int(raw)
        if number > SHOP_ID_OFFSET:
            return number
    else:
        parts = urlsplit(raw)
        if (parts.scheme != "https" or parts.netloc.lower() != SHOP_HOST
                or parts.query or parts.fragment):
            raise ValueError("Enter a SUMS product ID or an Imperial College Union shop URL.")
        match = SHOP_PATH.fullmatch(parts.path)
        if not match:
            raise ValueError("The Union shop URL must point to /shop/product/1234-/.")
        number = int(match.group(1))

    if not 0 < number < SHOP_ID_OFFSET:
        raise ValueError("Enter a valid shop product ID (for example, 3023).")
    return SHOP_ID_OFFSET + number


def resolve_product_id(union_product_id: int | None, shop_product_ref: str | None) -> int | None:
    """Keep existing API-selected ids untouched; resolve only a manual input."""
    if shop_product_ref is None:
        return union_product_id
    if union_product_id is not None:
        raise HTTPException(400, "Choose either an API product or a manual shop product reference.")
    try:
        return parse_shop_product_ref(shop_product_ref)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
