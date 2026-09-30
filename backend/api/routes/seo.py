"""robots.txt and sitemap.xml.

Generated rather than static files, because the interesting URLs — events and
resource pages — are rows in the database. A static sitemap would be stale the
moment the committee added an event.

eesoc.com publishes neither, so nothing tells a crawler which pages exist.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Response

from clients import database

router = APIRouter()

SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "https://eesoc.com").rstrip("/")

# Private sections. Listing them in robots.txt at least keeps crawlers from
# wasting requests on a login wall — it is not a security control, which is why
# each is also enforced server-side.
DISALLOWED = ["/admin", "/portal", "/api/"]

STATIC_PATHS = [
    ("/", "daily", "1.0"),
    ("/events", "daily", "0.9"),
    ("/resources", "weekly", "0.8"),
    ("/sponsors", "weekly", "0.6"),
    ("/jobs", "daily", "0.7"),
    ("/lockers", "monthly", "0.5"),
]


@router.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in DISALLOWED]
    lines += ["", f"Sitemap: {SITE_ORIGIN}/sitemap.xml", ""]
    return Response("\n".join(lines), media_type="text/plain")


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> Response:
    today = datetime.now(timezone.utc).date().isoformat()
    urls: list[str] = []

    def add(path: str, changefreq: str, priority: str, lastmod: str | None = None) -> None:
        urls.append(
            "<url>"
            f"<loc>{escape(SITE_ORIGIN + path)}</loc>"
            f"<lastmod>{lastmod or today}</lastmod>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            "</url>"
        )

    for path, freq, priority in STATIC_PATHS:
        add(path, freq, priority)

    # Only published rows: a draft event must not be discoverable, and an
    # unpublished resource page is not ready to be read.
    try:
        for event in await database.get("/events", {"published_only": True, "limit": 500}) or []:
            add(f"/events/{event['slug']}", "weekly", "0.7")
    except Exception:  # noqa: BLE001 - a sitemap must still render
        pass

    try:
        for page in await database.get("/resource-pages", {"published_only": True}) or []:
            add(
                f"/resources/{page['slug']}",
                "monthly",
                "0.6",
                (page.get("updated_at") or "")[:10] or None,
            )
    except Exception:  # noqa: BLE001
        pass

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    return Response(body, media_type="application/xml")
