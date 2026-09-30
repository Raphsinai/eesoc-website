"""Resolving a person to an account.

Shared by sign-in and by guest booking, so both agree on what makes somebody a
member. Duplicating this logic is how the two paths would quietly diverge.
"""

from __future__ import annotations

import logging
import re

from clients import database
from utils.auth import normalise_email

log = logging.getLogger("api.identity")

# Both are live Imperial domains. `ic.ac.uk` is the shorter alias; the members
# feed uses `imperial.ac.uk`, so an address on either must resolve to the same
# person.
IMPERIAL_DOMAINS = {"ic.ac.uk", "imperial.ac.uk"}


# An Imperial login: 1-5 letters then 1-5 digits (jbloggs50 -> no, ab1234 -> yes).
SHORTCODE = re.compile(r"^[a-z]{1,5}[0-9]{1,5}$")

# The canonical domain for internal accounts. Imperial issues addresses on both,
# and the members feed uses @imperial.ac.uk, but storing one form means an
# account can be found by exactly one key.
CANONICAL_DOMAIN = "ic.ac.uk"


def is_shortcode(value: str | None) -> bool:
    return bool(value and SHORTCODE.match(value.strip().lower()))


def canonical_email(email: str, shortcode: str | None = None, member: dict | None = None) -> str:
    """Reduce an Imperial identity to `shortcode@ic.ac.uk`.

    One person can arrive as ab1234@ic.ac.uk, ab1234@imperial.ac.uk or
    firstname.lastname24@imperial.ac.uk, and as a guest typing a shortcode into
    a form. Without a canonical form each of those becomes a separate account,
    and somebody who booked as a guest would not see that booking after logging
    in. Everything internal therefore collapses to one key.

    Non-Imperial addresses are returned unchanged.
    """
    address = normalise_email(email)
    local, _, domain = address.partition("@")

    if domain not in IMPERIAL_DOMAINS:
        return address

    # Best evidence first: the Union's own record of their login.
    if member and member.get("login"):
        return f"{member['login'].strip().lower()}@{CANONICAL_DOMAIN}"
    if is_shortcode(shortcode):
        return f"{shortcode.strip().lower()}@{CANONICAL_DOMAIN}"
    if is_shortcode(local):
        return f"{local}@{CANONICAL_DOMAIN}"

    # An Imperial address whose local part is a name (firstname.lastname24) and
    # with no membership record to map it — nothing to canonicalise against, so
    # leave it be rather than inventing a shortcode.
    return address


def is_imperial(email: str) -> bool:
    """True for a college address.

    Says nothing about membership — plenty of staff and alumni hold one without
    being ICU members. It only decides whether asking for a shortcode makes
    sense.
    """
    return normalise_email(email).rsplit("@", 1)[-1] in IMPERIAL_DOMAINS


def shortcode_from_email(email: str) -> str | None:
    """Imperial logins are the local part of the college address."""
    email = normalise_email(email)
    if "@" not in email or not is_imperial(email):
        return None
    return email.split("@", 1)[0] or None


def clean_shortcode(value: str | None) -> str | None:
    """Accept what people actually type: 'ab1234', 'AB1234', or the whole
    address."""
    if not value:
        return None
    value = value.strip().lower()
    if "@" in value:
        return shortcode_from_email(value) or value.split("@", 1)[0]
    return value or None


async def find_member(email: str, shortcode: str | None = None) -> dict | None:
    """Look a person up in the synced ICU membership feed.

    Tried in order of reliability: the address as given, then the shortcode they
    supplied, then the shortcode implied by an Imperial address. A member's ICU
    record may carry a personal address, so email alone is not enough.
    """
    candidates = [
        {"email": email},
        {"login": clean_shortcode(shortcode)} if shortcode else None,
        {"login": shortcode_from_email(email)} if is_imperial(email) else None,
    ]
    for params in candidates:
        if not params or not next(iter(params.values())):
            continue
        member = await database.get("/union/members/lookup", params)
        if member is not None:
            return member
    return None


async def resolve_or_create_user(
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    shortcode: str | None = None,
    verified: bool = False,
) -> dict:
    """Find or create the account for an address.

    `verified` records whether the person has actually proved they control it.
    Guest bookings create an *unverified* account: it owns the reservation and
    can be matched to a purchase, but it is not a login until they complete the
    code flow.

    The membership lookup runs every time, not just at creation — ICU membership
    is annual, so somebody who has joined since their last visit gets upgraded.
    """
    address = normalise_email(email)
    member = await find_member(address, shortcode)
    # Collapse Imperial identities to shortcode@ic.ac.uk so a guest booking and
    # a later sign-in land on the same account.
    address = canonical_email(address, shortcode, member)

    payload: dict = {"email": address}
    if member is not None:
        # Trust the Union's spelling of a member's name over anything typed in.
        payload["member_id"] = member["id"]
        payload["first_name"] = member.get("first_name") or first_name
        payload["last_name"] = member.get("last_name") or last_name
        payload["shortcode"] = member.get("login") or clean_shortcode(shortcode)
    else:
        payload["first_name"] = first_name
        payload["last_name"] = last_name
        payload["shortcode"] = clean_shortcode(shortcode) or shortcode_from_email(address)

    if verified:
        payload["email_verified"] = True

    return await database.post("/auth/users/upsert", payload)
