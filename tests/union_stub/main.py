"""ICU bridge stub — the test stack's stand-in for backend/union/main.py.

Serves the same routes with the same snake_case response shapes, from the
canned rows in fixtures.py, and makes no network calls of any kind. It exists
because the real bridge's whole job is talking to eActivities, which is
rate-limited per source IP and bans the whole society on a breach — so the
test stack (CI included) must never touch it. No ICU_API_KEY exists in this
service's environment (docker-compose.test.yml deliberately gives it no
env_file), and its /health says "stub" so a test can tell it apart from the
real thing.
"""

from datetime import datetime, timezone

from fastapi import FastAPI, Request

import fixtures

app = FastAPI(
    title="EESoc ICU bridge (stub)",
    description="Canned eActivities fixtures. Never talks to the Union.",
)

# Request counting, added for the section-02 rollout tests (tests/zero-downtime).
#
# The daily union sync must run on exactly one api pod even while a
# RollingUpdate has two pods alive at once — the api's in-process lock cannot
# see the other pod, so the claim lives in Postgres (see
# backend/api/services/sync.py). The test for that needs ground truth about
# how many times the stub was actually called, which only the stub itself can
# report. Plain in-process counters are that ground truth: the stub runs as a
# single process, so the numbers cannot race or drift.
_REQUEST_COUNTS: dict[str, int] = {}

# The counter's own endpoints are excluded, so a test polling /counts does not
# inflate the numbers it is about to assert on.
_COUNTED_EXCLUSIONS = {"/counts", "/counts/reset"}


@app.middleware("http")
async def _count_requests(request: Request, call_next):
    # Keyed on the path without its query string: the sync passes ?year=..., and
    # what the tests care about is which report was pulled, not with what
    # arguments.
    if request.url.path not in _COUNTED_EXCLUSIONS:
        _REQUEST_COUNTS[request.url.path] = _REQUEST_COUNTS.get(request.url.path, 0) + 1
    return await call_next(request)


@app.get("/counts")
async def request_counts() -> dict:
    """How many times each stub path has been requested since the last reset."""
    return {"total": sum(_REQUEST_COUNTS.values()), "requests": dict(_REQUEST_COUNTS)}


@app.post("/counts/reset")
async def reset_request_counts() -> dict:
    """Zero the counters, so a test can measure exactly one sync run's traffic."""
    _REQUEST_COUNTS.clear()
    return {"ok": True}


def academic_year(when: datetime | None = None) -> str:
    """ICU's year, 1 August to 31 July, in eActivities' 'xx-xx' form.

    Duplicated from backend/api/utils/year.py rather than shared, because the
    real union service keeps its own copy too — the two are separate
    deployables, and this stub replaces one of them.
    """
    now = when or datetime.now(timezone.utc)
    year = now.year % 100
    if now.month >= 8:
        return f"{year:02d}-{(year + 1) % 100:02d}"
    return f"{(year - 1) % 100:02d}-{year:02d}"


@app.get("/health")
async def health() -> dict:
    # Same shape as the real bridge's liveness answer, plus the marker that
    # says which one is running — a fixture asserting on this can fail loudly
    # if somebody points the test stack at the real bridge by mistake.
    return {"ok": True, "stub": True, "academic_year": academic_year()}


@app.get("/academic-year")
async def current_academic_year() -> dict:
    return {"academic_year": academic_year()}


@app.get("/members")
async def members(year: str | None = None, force: bool = False) -> list[dict]:
    year = year or academic_year()
    return [
        {
            "cid": str(m.get("CID") or "").strip(),
            "first_name": m.get("FirstName"),
            "last_name": m.get("Surname"),
            "email": (m.get("Email") or "").strip() or None,
            "login": (m.get("Login") or "").strip() or None,
            "order_no": None if m.get("OrderNo") is None else str(m.get("OrderNo")),
            "member_type": m.get("MemberType"),
            "academic_year": year,
        }
        for m in fixtures.MEMBERS
        if m.get("CID")
    ]


@app.get("/committee")
async def committee(year: str | None = None, force: bool = False) -> list[dict]:
    year = year or academic_year()
    return [
        {
            "cid": str(m.get("CID") or "").strip(),
            "first_name": m.get("FirstName"),
            "last_name": m.get("Surname"),
            "email": (m.get("Email") or "").strip() or None,
            "login": (m.get("Login") or "").strip() or None,
            "post_name": m.get("PostName") or "Committee member",
            "phone_no": (m.get("PhoneNo") or "").strip() or None,
            "start_date": m.get("StartDate"),
            "end_date": m.get("EndDate"),
            "academic_year": year,
        }
        for m in fixtures.COMMITTEE
        if m.get("CID")
    ]


@app.get("/products")
async def products(year: str | None = None, force: bool = False) -> list[dict]:
    """Flattened to one row per product line, like the real bridge: a line is
    what a sale references and what an admin links a ticket type to."""
    year = year or academic_year()
    out: list[dict] = []
    for p in fixtures.PRODUCTS:
        for line in p.get("ProductLines") or []:
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
                    "selling_date_start": None,
                    "selling_date_end": None,
                    "url": None,
                }
            )
    return [r for r in out if r["product_id"] and r["product_line_id"]]


@app.get("/sales")
async def sales(year: str | None = None, force: bool = False) -> list[dict]:
    return []


@app.post("/cache/clear")
async def clear_cache() -> dict:
    # Nothing is cached, but the endpoint exists so callers (and tests) can
    # exercise the same surface as the real bridge.
    return {"ok": True}


@app.get("/whatson")
async def whats_on(force: bool = False) -> list[dict]:
    return []
