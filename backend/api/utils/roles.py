"""What each committee role may do.

One table, read by `utils/deps.py` and reflected back to the SPA so the admin
page can hide what it cannot use. Keeping it here rather than scattering
`if session.role == ...` through the routes means the answer to "who can delete
a sponsor" is one grep, and adding a role is one line.

Capabilities, not endpoints — endpoints come and go, and a capability survives
the refactor that splits one route into three:

  users       grant and revoke roles, impersonate. Superuser only.
  events      create, edit and delete ANY event, its tickets and its bookings.
  industry    the same, but only for events flagged as industry events: the
              careers fair, a sponsor's talk, a site visit. Strictly narrower
              than `events` — see `deps.may_edit_event`.
  sponsors    sponsors, their portal access, their listings and requests.
  site        everything that is just the website: resources, files, lockers,
              Union product links, the sync buttons, bulk email, the committee
              page's layout and other people's cards.
  passkey_links
              make a one-off link that lets somebody save a passkey on an
              account without an emailed code — for the people our codes
              never reach. Whoever opens the link gets into that account, so
              it is held by the two roles trusted with the whole site, and a
              link for a committee account needs `users` as well (see
              `routes/passkeys.py`).
  locker_allowances
              give somebody free lockers for the year, on top of any they
              buy. Superuser only: a locker is otherwise paid for, so this is
              giving away the society's money, and it is kept to the people
              who also decide who has access at all.

A committee member editing *their own* card needs none of these. That is
ownership of a row, not a role, and it is resolved in `deps.committee_card`.
"""

from __future__ import annotations

# Ordered most to least privileged, which is also the order the People tab
# lists them in.
ROLES: dict[str, dict] = {
    "superuser": {
        "label": "Superuser",
        "blurb": "Everything, including granting roles and signing in as somebody else.",
        "capabilities": {
            "users", "events", "sponsors", "site", "passkey_links", "locker_allowances",
        },
    },
    "admin": {
        "label": "Admin",
        "blurb": "Everything except managing who has access.",
        "capabilities": {"events", "sponsors", "site", "passkey_links"},
    },
    "events": {
        "label": "Events",
        "blurb": "Events, tickets and bookings.",
        "capabilities": {"events"},
    },
    "industry": {
        "label": "Industry",
        "blurb": "Sponsors, and the events marked as industry events.",
        "capabilities": {"sponsors", "industry"},
    },
}

ROLE_NAMES = tuple(ROLES)


def capabilities(role: str | None) -> frozenset[str]:
    if not role:
        return frozenset()
    return frozenset(ROLES.get(role, {}).get("capabilities", ()))


def describe() -> list[dict]:
    """The role list for the People tab, so the UI never hardcodes it."""
    return [
        {
            "id": name,
            "label": spec["label"],
            "blurb": spec["blurb"],
            "capabilities": sorted(spec["capabilities"]),
        }
        for name, spec in ROLES.items()
    ]
