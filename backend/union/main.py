"""ICU bridge.

Translates eActivities' PascalCase payloads into the snake_case shapes the rest
of the backend stores, and shields the upstream rate limit behind a cache. The
api service is the only caller.
"""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import client
from utils import academic_year, parse_dt

log = logging.getLogger("union")

app = FastAPI(
    title="EESoc ICU bridge",
    description="Read-only view of eActivities, cached and throttled.",
)


def _fail_if_unconfigured() -> None:
    if not client.configured():
        raise HTTPException(
            503,
            "ICU bridge is not configured — set ICU_API_KEY and ICU_CENTRE_CODE.",
        )


@app.get("/health")
async def health() -> dict:
    return {"ok": True, **client.cache_state()}


@app.get("/academic-year")
async def current_academic_year() -> dict:
    return {"academic_year": academic_year()}


@app.get("/members")
async def members(year: str | None = None, force: bool = False) -> list[dict]:
    """GET /CSP/{centre}/reports/members"""
    _fail_if_unconfigured()
    year = year or academic_year()
    try:
        raw = await client.get(
            client.centre_path("/reports/members"), {"year": year}, force=force
        )
    except client.UnionAPIError as exc:
        raise HTTPException(502, exc.message)

    return [
        {
            "cid": str(m.get("CID") or "").strip(),
            "first_name": m.get("FirstName"),
            "last_name": m.get("Surname"),
            "email": (m.get("Email") or "").strip() or None,
            "login": (m.get("Login") or "").strip() or None,
            # OrderNo is an int in the docs but real order numbers contain
            # letters, so it is carried as text throughout.
            "order_no": None if m.get("OrderNo") is None else str(m.get("OrderNo")),
            "member_type": m.get("MemberType"),
            "academic_year": year,
        }
        for m in raw
        if m.get("CID")
    ]


@app.get("/committee")
async def committee(year: str | None = None, force: bool = False) -> list[dict]:
    """GET /CSP/{centre}/reports/committee"""
    _fail_if_unconfigured()
    year = year or academic_year()
    try:
        raw = await client.get(
            client.centre_path("/reports/committee"), {"year": year}, force=force
        )
    except client.UnionAPIError as exc:
        raise HTTPException(502, exc.message)

    return [
        {
            "cid": str(m.get("CID") or "").strip(),
            "first_name": m.get("FirstName"),
            "last_name": m.get("Surname"),
            "email": (m.get("Email") or "").strip() or None,
            "login": (m.get("Login") or "").strip() or None,
            "post_name": m.get("PostName") or "Committee member",
            "phone_no": (m.get("PhoneNo") or "").strip() or None,
            "start_date": parse_dt(m.get("StartDate")),
            "end_date": parse_dt(m.get("EndDate")),
            "academic_year": year,
        }
        for m in raw
        if m.get("CID")
    ]


@app.get("/products")
async def products(year: str | None = None, force: bool = False) -> list[dict]:
    """GET /CSP/{centre}/reports/products

    Deliberately the *reports* endpoint: /products is not populated for our
    centre, and only the reports variant accepts a year.

    Products are flattened to one row per product line, because a line is what a
    sale actually references and what an admin links a ticket type to.
    """
    _fail_if_unconfigured()
    year = year or academic_year()
    try:
        raw = await client.get(
            client.centre_path("/reports/products"), {"year": year}, force=force
        )
    except client.UnionAPIError as exc:
        raise HTTPException(502, exc.message)

    out: list[dict] = []
    for p in raw:
        lines = p.get("ProductLines") or []
        if not lines:
            # A product with no lines still needs a row so it can be linked.
            lines = [{"ID": p.get("ID"), "Name": p.get("Name"), "Price": None}]
        for line in lines:
            out.append(
                {
                    "product_id": p.get("ID"),
                    "product_line_id": line.get("ID"),
                    "academic_year": year,
                    "product_name": p.get("Name"),
                    "line_name": line.get("Name"),
                    "description": p.get("Description"),
                    "price": line.get("Price"),
                    "active": p.get("Active"),
                    "selling_date_start": parse_dt(p.get("SellingDateStart")),
                    "selling_date_end": parse_dt(p.get("SellingDateEnd")),
                    "url": p.get("URL"),
                }
            )
    return [r for r in out if r["product_id"] and r["product_line_id"]]


@app.get("/sales")
async def sales(year: str | None = None, force: bool = False) -> list[dict]:
    """GET /CSP/{centre}/reports/onlinesales

    This feed is the evidence a reservation was paid for. It refreshes once a
    day at 06:00, which is why holds are given a multi-day window.
    """
    _fail_if_unconfigured()
    year = year or academic_year()
    try:
        raw = await client.get(
            client.centre_path("/reports/onlinesales"), {"year": year}, force=force
        )
    except client.UnionAPIError as exc:
        raise HTTPException(502, exc.message)

    out = []
    for s in raw:
        customer = s.get("Customer") or {}
        out.append(
            {
                "order_number": str(s.get("OrderNumber") or "").strip(),
                "academic_year": year,
                "sale_datetime": parse_dt(s.get("SaleDateTime")),
                "product_id": s.get("ProductID"),
                "product_line_id": s.get("ProductLineID"),
                "price": s.get("Price"),
                "quantity": s.get("Quantity") or 1,
                "quantity_collected": s.get("QuantityCollected") or 0,
                "customer_cid": (str(customer.get("CID") or "").strip() or None),
                "customer_first_name": customer.get("FirstName"),
                "customer_last_name": customer.get("Surname"),
                # Emails in this feed arrive padded with spaces in the docs'
                # own examples, so everything is stripped on the way in.
                "customer_email": (customer.get("Email") or "").strip() or None,
                "customer_login": (customer.get("Login") or "").strip() or None,
            }
        )
    return [r for r in out if r["order_number"] and r["product_id"]]


@app.post("/cache/clear")
async def clear_cache() -> dict:
    client.clear_cache()
    return {"ok": True}


@app.get("/whatson")
async def whats_on(force: bool = False) -> list[dict]:
    """GET /CSP/{centre}/whatson — ICU's own event listing.

    Not used for booking (that is our `events` table), but useful for admins
    cross-checking what the Union has published.
    """
    _fail_if_unconfigured()
    try:
        raw = await client.get(client.centre_path("/whatson"), force=force)
    except client.UnionAPIError as exc:
        raise HTTPException(502, exc.message)
    return [
        {
            "union_event_id": e.get("ID"),
            "title": e.get("Title"),
            "description": e.get("Description"),
            "starts_at": parse_dt(e.get("EventStart")),
            "ends_at": parse_dt(e.get("EventEnd")),
            "location": e.get("Location"),
            "post_code": e.get("PostCode"),
            "event_type": e.get("EventType"),
            "active": e.get("Active"),
        }
        for e in raw
    ]


class ExternalMembersRequest(BaseModel):
    centre_code: str
    credential: str
    year: str

@app.post('/external-members')
async def external_members(req: ExternalMembersRequest):
    """Private bridge. The same global upstream rate limiter governs all societies."""
    try:
        raw = await client.external_members(req.centre_code,req.credential,req.year)
    except client.UnionAPIError as exc:
        raise HTTPException(502, 'Union refused membership request') from exc
    if not isinstance(raw,list):
        raise HTTPException(502,'Union returned an invalid membership report')
    return [{
        'cid':str(m.get('CID') or '').strip(),
        'login':str(m.get('Login') or '').strip().lower() or None,
        'first_name':m.get('FirstName'),
        'last_name':m.get('Surname'),
        'email':str(m.get('Email') or '').strip().lower() or None,
        'order_no':None if m.get('OrderNo') is None else str(m['OrderNo']).strip(),
        'member_type':m.get('MemberType'),
        # Keep any additional Union fields without pretending that this is
        # an EESoc membership or replacing the authenticated account record.
        'union_profile':m,
    } for m in raw if isinstance(m,dict) and str(m.get('CID') or '').strip()]
