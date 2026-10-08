"""Cache headers served by the frontend's nginx (AC 3 of section 03).

These are plain HTTP (curl-style) checks against the same `frontend` service the
browser hits: port 8080 on the compose TEST stack, where nginx serves the built
SPA and proxies /api. They pin three facts the asset-continuity design relies
on:

- the app shell (index.html) must never be long-cached, or a reload would keep
  serving a stale bundle and the chunk-recovery hook could never pick up the new
  deploy;
- hashed /assets/* files may be cached for a year as immutable, because their
  filenames change when their content does;
- the media location's own headers must not regress while the /assets location
  is added alongside it.

The client is a module-local fixture (not the one in tests/conftest.py) because
it points at the *frontend origin* rather than the api paths; the base URL is
overridable via EESOC_FRONTEND_BASE the same way the shared fixtures accept
EESOC_API_BASE etc. Tests never read .env — the frontend origin needs no
secrets and is baked into the compose port mapping.
"""

import os
import re

import httpx
import pytest

FRONTEND_BASE = os.environ.get("EESOC_FRONTEND_BASE", "http://localhost:8080")


@pytest.fixture(scope="module")
def frontend() -> httpx.Client:
    """A plain client at the frontend origin, with no redirect following.

    follow_redirects=False matters: assertions read the response nginx itself
    produced (status + headers) rather than whatever a redirect would resolve
    to, so a caching mis-configuration is not papered over by a 200 that came
    from somewhere else.
    """
    return httpx.Client(base_url=FRONTEND_BASE, timeout=10.0, follow_redirects=False)


def test_index_html_is_not_long_cacheable_and_revalidates(frontend):
    """The app shell is no-cache with an ETag, and a conditional GET is 304.

    "/" has no file of its own; nginx's try_files falls through to /index.html,
    so both URLs must land in the no-cache location. The ETag + 304 matters
    because `no-cache` alone would force a full re-fetch on every reload; the
    combination revalidates cheaply while still always asking the server about
    a new deploy.
    """
    for path in ("/", "/index.html"):
        response = frontend.get(path)
        assert response.status_code == 200, f"{path} must serve the shell"
        cache_control = response.headers.get("cache-control", "")
        # The shell must revalidate on every request — never be stored long.
        assert "no-cache" in cache_control, f"{path} Cache-Control does not say no-cache"
        assert "max-age=31536000" not in cache_control, f"{path} must not be year-cached"

    first = frontend.get("/index.html")
    etag = first.headers.get("etag")
    assert etag, "the shell response must carry an ETag for revalidation"
    conditional = frontend.get("/index.html", headers={"If-None-Match": etag})
    assert conditional.status_code == 304, "a conditional GET must revalidate to 304"


def test_hashed_asset_is_immutable_with_long_max_age_and_security_headers(frontend):
    """One real /assets/*.js URL from the built shell carries immutable caching.

    The build emits content-hashed filenames, so the entry script URL is only
    knowable by parsing the served shell — asserting on a hard-coded example
    would drift the moment the build changes. The entry script is read out of
    index.html the same way a browser does.
    """
    index = frontend.get("/")
    match = re.search(r'src="(/assets/[^"]+\.js)"', index.text)
    assert match, "the built index.html must reference a hashed /assets/*.js entry script"

    response = frontend.get(match.group(1))
    assert response.status_code == 200, "hashed asset must be served"
    cache_control = response.headers.get("cache-control", "")
    # `immutable` is the point of the exercise: it stops even a manual
    # revalidation of a chunk an old tab still needs (see the nginx.conf note).
    assert "immutable" in cache_control, "asset Cache-Control must include immutable"
    assert "max-age=31536000" in cache_control, "asset Cache-Control must carry the year-long max-age"

    # Regression guard for nginx's add_header inheritance trap: an add_header
    # inside a location resets every server-scope add_header, so the security
    # headers must be repeated inside /assets/ (see frontend/nginx.conf). If a
    # future edit drops them there, these three fail and name the header.
    assert response.headers.get("x-content-type-options") == "nosniff", \
        "/assets/ must keep X-Content-Type-Options: nosniff"
    assert response.headers.get("x-frame-options"), \
        "/assets/ must keep the X-Frame-Options security header"
    assert response.headers.get("referrer-policy"), \
        "/assets/ must keep the Referrer-Policy security header"


def test_media_location_keeps_cache_and_nosniff_headers_on_missing_file(frontend):
    """A missing media file 404s but still answers with the media cache headers.

    Media files are served straight off the shared volume (never proxied
    through the api), so a typo or a deleted upload must not change how the
    location caches things — the 404 response still has to advertise the
    media caching policy and the nosniff hardening. NOTE: for the Cache-Control
    header to survive a 404 the /media/ location's add_header needs `always`
    (nginx only emits a plain add_header on 2xx/3xx), matching the section's
    "media caching must not regress" criterion.
    """
    response = frontend.get("/media/__spa_cache_headers_missing_404__.png")
    assert response.status_code == 404, "an unknown media path must 404, not fall back to the SPA shell"

    # nosniff is server-scope (with `always`), so it must appear even on 404.
    assert response.headers.get("x-content-type-options") == "nosniff", \
        "media 404 must keep X-Content-Type-Options: nosniff"

    cache_control = response.headers.get("cache-control", "")
    assert "public" in cache_control, "media 404 must keep Cache-Control: public"
    assert "max-age=2592000" in cache_control, "media 404 must keep the 30-day max-age"