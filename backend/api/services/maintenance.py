"""Which features are actually usable right now, and what to tell people.

A half-working feature is worse than an absent one: a member who books a ticket
and never receives a confirmation emails the committee, and a member who
requests a sign-in code that cannot be sent simply assumes the site is broken.
So anything whose dependencies are missing is declared unavailable up front —
the API refuses it with a 503 and a plain-English reason, and the UI says
"under maintenance" instead of offering a control that will fail.

The set is *derived*, not hard-coded. It is computed from what the process can
actually do, so restoring the dependency restores the feature with no code
change and no redeploy — the api picks it up on its next restart. That matters
here specifically: the webmaster mailbox password is force-reset by Imperial
every year, and when it lapses the honest behaviour is for the site to say so
rather than to keep accepting bookings it cannot confirm.

MAINTENANCE_FEATURES forces extra keys off by hand, for the case where
something is broken in a way the process cannot detect.
"""

from __future__ import annotations

import logging

from config import settings
from utils import email

log = logging.getLogger("api.maintenance")

# Keys the frontend knows about. The label is what a member sees.
FEATURES: dict[str, str] = {
    "login": "Signing in",
    "bookings": "Event booking",
    "lockers": "Locker booking",
    "sponsor_portal": "The sponsor portal",
    "admin": "The admin dashboard",
}

# Deliberately NOT gated on email, even though both are unreachable while
# sign-in is down:
#
#   admin          — the annual password reset breaks email, and the person who
#                    has to fix it is an admin. Taking the dashboard away from
#                    an already-signed-in admin at exactly that moment is how a
#                    one-command fix becomes an outage.
#   sponsor_portal — needs sign-in, but nothing in it sends mail, so an existing
#                    session keeps working.
#   lockers        — booking is self-service; only the yearly clear-out notice
#                    needs email, and that is an admin action, not a member one.
#
# Bookings ARE gated: confirmations, purchase reminders and the whole waitlist
# are email. A booking nobody can be told about is not a booking.
NEEDS_EMAIL = ("login", "bookings")

_MESSAGES = {
    "login": (
        "We can't send sign-in codes at the moment, so signing in is "
        "temporarily unavailable. It'll be back shortly."
    ),
    "bookings": (
        "Event booking is temporarily unavailable — we can't send confirmation "
        "emails right now, and we'd rather not take a booking we can't confirm."
    ),
}

_DEFAULT_MESSAGE = "This is temporarily unavailable while we sort something out."


def _forced() -> set[str]:
    """Keys named in MAINTENANCE_FEATURES."""
    raw = (settings.maintenance_features or "").replace(",", " ").split()
    forced = set()
    for key in raw:
        key = key.strip().lower()
        if not key:
            continue
        if key not in FEATURES:
            # A typo here would silently disable nothing, which is the failure
            # mode where someone believes a feature is off and it is not.
            log.warning(
                "MAINTENANCE_FEATURES lists unknown feature %r; known: %s",
                key, ", ".join(sorted(FEATURES)),
            )
            continue
        forced.add(key)
    return forced


def unavailable() -> dict[str, str]:
    """Feature key -> the reason a member should be shown."""
    out: dict[str, str] = {}

    if not email.configured():
        for key in NEEDS_EMAIL:
            out[key] = _MESSAGES.get(key, _DEFAULT_MESSAGE)

    for key in _forced():
        out.setdefault(key, settings.maintenance_message or _DEFAULT_MESSAGE)

    return out


def is_available(feature: str) -> bool:
    return feature not in unavailable()


def status() -> dict:
    """Payload for GET /status — what the SPA renders its banners from."""
    down = unavailable()
    return {
        "features": {
            key: {
                "label": label,
                "available": key not in down,
                "reason": down.get(key),
            }
            for key, label in FEATURES.items()
        },
        "unavailable": sorted(down),
        # One line for a site-wide banner, so the header can say something
        # without the SPA having to reason about which features matter.
        "notice": (
            next(iter(down.values())) if len(down) == 1
            else "Some parts of the site are temporarily unavailable." if down
            else None
        ),
    }


def log_startup_state() -> None:
    down = unavailable()
    if down:
        log.warning(
            "UNDER MAINTENANCE: %s. Public browsing is unaffected.",
            ", ".join(f"{k} ({FEATURES[k]})" for k in sorted(down)),
        )
    else:
        log.info("All features available")
