"""Committee page.

Names and roles come from the Union's committee feed; the intro paragraph and
the photographs are ours. The two are joined by CID so a re-sync never clobbers
an uploaded photo.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from clients import database
from models.auth import AuthSession
from utils.deps import require_admin, verify_csrf
from utils.year import academic_year

router = APIRouter()


@router.get("/committee")
async def get_committee() -> dict:
    year = academic_year()
    members = await database.get("/union/committee", {"academic_year": year}) or []
    intro = await database.get("/union/content/committee.intro")

    people = [
            {
                "cid": m["cid"],
                "role": m.get("display_role") or m["post_name"],
                "name": " ".join(
                    p for p in [m.get("first_name"), m.get("last_name")] if p
                ),
                "intro": m.get("intro", ""),
                "photoUrl": m.get("photo_url"),
                "row": m.get("row_number", 1),
            }
            for m in members
    ]

    # Grouped into the rows the committee chose. `committee` is kept as a flat
    # list too so anything already consuming it keeps working.
    rows: dict[int, list] = {}
    for person in people:
        rows.setdefault(person["row"], []).append(person)

    return {
        "intro": (intro or {}).get("value", ""),
        "academicYear": year,
        "committee": people,
        "rows": [
            {"row": number, "members": rows[number]} for number in sorted(rows)
        ],
    }


@router.put("/committee/intro", dependencies=[Depends(verify_csrf)])
async def set_intro(
    value: str = Body(embed=True), session: AuthSession = Depends(require_admin)
) -> dict:
    await database.put(
        "/union/content/committee.intro",
        {"key": "committee.intro", "value": value},
        params={"updated_by": session.user.id},
    )
    return {"ok": True}


@router.put("/committee/{login}/profile", dependencies=[Depends(verify_csrf)])
async def set_profile(
    login: str,
    intro: str = Body(default="", embed=True),
    photo_url: str | None = Body(default=None, embed=True),
    display_role: str | None = Body(default=None, embed=True),
    row_number: int = Body(default=1, embed=True),
    sort_order: int = Body(default=100, embed=True),
    cid: str | None = Body(default=None, embed=True),
    hidden: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    """The editable half of a committee entry: intro, photo, display role, order.

    Keyed on login, so it survives a roster re-sync — that is what identifies
    the same person. The *name* stays read-only: it belongs to the Union's
    records, and editing it here would only create a divergence the next sync
    reverts. The role can be overridden for display, because ICU's post names
    are sometimes too long for the card.
    """
    return await database.put(
        f"/union/committee/profiles/{login}",
        {
            "login": login,
            "cid": cid,
            "intro": intro,
            "photo_url": photo_url,
            "display_role": display_role,
            "row_number": max(1, row_number),
            "sort_order": sort_order,
            # Carried explicitly: omitting it would default to False and
            # quietly un-hide someone on the next unrelated edit.
            "hidden": hidden,
        },
    )


@router.get("/admin/committee")
async def admin_committee(session: AuthSession = Depends(require_admin)) -> dict:
    """Roster plus overlay, for the admin editor."""
    year = academic_year()
    # include_hidden: this is the only place a hidden person can be
    # restored, so it has to be able to see them.
    members = (
        await database.get(
            "/union/committee", {"academic_year": year, "include_hidden": True}
        )
        or []
    )
    return {
        "academicYear": year,
        "committee": [
            {
                "cid": m["cid"],
                "login": m.get("login"),
                "name": " ".join(
                    p for p in [m.get("first_name"), m.get("last_name")] if p
                ),
                "unionRole": m["post_name"],
                "displayRole": m.get("display_role") or "",
                "email": m.get("email"),
                "intro": m.get("intro", ""),
                "photoUrl": m.get("photo_url"),
                "row": m.get("row_number", 1),
                "sortOrder": m.get("sort_order", 100),
                "hidden": m.get("hidden", False),
            }
            for m in members
        ],
    }
