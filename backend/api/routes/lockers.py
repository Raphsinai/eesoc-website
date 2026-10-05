"""Locker map and booking.

Members only, and only if a locker has been bought on the Union shop. A member
may buy more than one, so entitlement is a count.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database
from models.auth import AuthSession
from utils.deps import current_session, require_feature, require_member, require_session, verify_csrf
from utils.year import academic_year

router = APIRouter(prefix="/lockers")

REASONS = {
    "taken": "Somebody just took that one. Pick another.",
    "not_member": "Lockers are for Union members.",
    "inactive": "That locker is out of service.",
    "no_entitlement": (
        "We can't find a locker purchase against your Union account. Buy one on "
        "the Union shop — purchases reach us the following morning."
    ),
}


@router.get("")
async def list_lockers(session: AuthSession | None = Depends(current_session)) -> dict:
    """Bank map, grouped the way the page renders it."""
    user_id = session.user.id if session else None
    lockers = await database.get("/lockers", {"user_id": user_id}) or []

    banks: dict[str, list] = defaultdict(list)
    for locker in lockers:
        banks[locker["bank"]].append(
            {
                "id": f"{locker['bank']}-{locker['number']}",
                "lockerId": locker["id"],
                "state": locker["state"],
                "mine": locker["mine"],
            }
        )

    out = []
    for letter in sorted(banks):
        items = banks[letter]
        free = sum(1 for i in items if i["state"] == "free")
        out.append(
            {
                "letter": letter,
                # "available", not "free": lockers are paid for, and the
                # same word for "vacant" reads as "no charge".
                "note": f"{free} of {len(items)} available",
                "lockers": items,
            }
        )

    entitlement = None
    if session and session.user.member_id is not None:
        entitlement = await database.get(
            "/lockers/entitlement",
            {"user_id": session.user.id, "academic_year": academic_year()},
        )

    return {"banks": out, "entitlement": entitlement}


@router.get("/me")
async def my_lockers(session: AuthSession = Depends(require_session)) -> dict:
    bookings = await database.get("/lockers/bookings", {"user_id": session.user.id}) or []
    entitlement = await database.get(
        "/lockers/entitlement",
        {"user_id": session.user.id, "academic_year": academic_year()},
    )
    return {
        "bookings": [
            {
                "id": b["id"],
                "locker": f"{b.get('bank')}-{b.get('number')}",
                "since": b.get("created_at"),
            }
            for b in bookings
        ],
        "entitlement": entitlement,
    }


@router.post(
    "/{locker_id}/book",
    dependencies=[Depends(verify_csrf), require_feature("lockers")],
)
async def book(
    locker_id: int, session: AuthSession = Depends(require_member)
) -> dict:
    """Claim a locker.

    The entitlement check and the uniqueness guard both live in the adapter — the
    latter as a partial unique index, so two people clicking the same locker at
    the same instant cannot both win.
    """
    result = await database.post(
        "/lockers/book",
        {
            "locker_id": locker_id,
            "user_id": session.user.id,
            "academic_year": academic_year(),
        },
    )
    if not result["ok"]:
        status = 409 if result["reason"] == "taken" else 403
        raise HTTPException(status, REASONS.get(result["reason"], "Could not book."))
    return {"ok": True, "bookingId": result["booking"]["id"]}


@router.post("/bookings/{booking_id}/release", dependencies=[Depends(verify_csrf)])
async def release(
    booking_id: int, session: AuthSession = Depends(require_session)
) -> dict:
    bookings = await database.get(
        "/lockers/bookings", {"user_id": session.user.id}
    ) or []
    if not any(b["id"] == booking_id for b in bookings) and not session.is_admin:
        raise HTTPException(404, "No such booking")
    await database.post(f"/lockers/bookings/{booking_id}/release")
    return {"ok": True}
