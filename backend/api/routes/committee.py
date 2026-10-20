"""Committee page.

Names and roles come from the Union's committee feed; the intro paragraph and
the photographs are ours. The two are joined by CID so a re-sync never clobbers
an uploaded photo.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from clients import database
from models.auth import AuthSession
from services import storage
from utils.deps import (
    require_action,
    committee_card,
    require_session,
    require_committee_page,
    verify_csrf,
)
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


@router.put("/committee/intro", dependencies=[Depends(require_action("committee.intro")), Depends(verify_csrf)])
async def set_intro(
    value: str = Body(embed=True), session: AuthSession = Depends(require_committee_page)
) -> dict:
    await database.put(
        "/union/content/committee.intro",
        {"key": "committee.intro", "value": value},
        params={"updated_by": session.user.id},
    )
    return {"ok": True}


@router.put("/committee/{login}/profile", dependencies=[Depends(require_action("committee.profiles")), Depends(verify_csrf)])
async def set_profile(
    login: str,
    intro: str = Body(default="", embed=True),
    photo_url: str | None = Body(default=None, embed=True),
    display_role: str | None = Body(default=None, embed=True),
    row_number: int = Body(default=1, embed=True),
    sort_order: int = Body(default=100, embed=True),
    cid: str | None = Body(default=None, embed=True),
    hidden: bool = Body(default=False, embed=True),
    user_email: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_committee_page),
) -> dict:
    """The editable half of a committee entry: intro, photo, display role, order.

    Keyed on login, so it survives a roster re-sync — that is what identifies
    the same person. The *name* stays read-only: it belongs to the Union's
    records, and editing it here would only create a divergence the next sync
    reverts. The role can be overridden for display, because ICU's post names
    are sometimes too long for the card.

    `user_email` hands the card to an account that would not otherwise be
    matched to it — see PUT /committee/me.
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
            "user_email": (user_email or "").strip().lower() or None,
        },
    )


# ------------------------------------------------------------- my own card


@router.get("/committee/me")
async def my_card(session: AuthSession = Depends(require_session)) -> dict:
    """The card this person may edit, if the roster has one for them.

    Answers `{"card": null}` rather than 404 for somebody who is not on
    committee: it is not an error to ask, and the header asks on every load.
    """
    entry = await committee_card(session)
    if entry is None:
        return {"card": None}
    return {"card": _own(entry)}


@router.put("/committee/me", dependencies=[Depends(verify_csrf)])
async def update_my_card(
    intro: str = Body(default="", embed=True),
    photo_url: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_session),
) -> dict:
    """Edit your own blurb and photograph.

    Intro and photo only. The display role, the row and the ordering are how
    the page is laid out rather than what is on the card, and letting twenty
    people each nudge their own position is how the grid ends up with four
    people claiming row one. `hidden` is likewise not self-serve: leaving
    committee is something the committee records, not something you do to
    yourself on the way out.

    Everything ICU owns — name, post, CID — is carried through from the roster
    unchanged, so saving here cannot blank a field the editor never showed.
    """
    entry = await committee_card(session)
    if entry is None:
        raise HTTPException(
            403,
            "You are not on this year's committee roster. If you are, ask an "
            "admin to link your email to your card.",
        )

    await database.put(
        f"/union/committee/profiles/{entry['login']}",
        {
            "login": entry["login"],
            "cid": entry.get("cid"),
            "intro": intro,
            "photo_url": photo_url,
            "display_role": entry.get("display_role"),
            "row_number": max(1, entry.get("row_number") or 1),
            "sort_order": entry.get("sort_order") or 100,
            "hidden": bool(entry.get("hidden")),
            "user_email": entry.get("user_email"),
        },
    )
    return {"ok": True, "card": _own({**entry, "intro": intro, "photo_url": photo_url})}


@router.post("/committee/me/photo", dependencies=[Depends(verify_csrf)])
async def upload_my_photo(
    file: UploadFile = File(...), session: AuthSession = Depends(require_session)
) -> dict:
    """Upload your own committee photograph.

    Its own endpoint rather than a widened `/admin/uploads`: a committee member
    with no admin role still needs to put a face on their card, and that is not
    a reason to give them the file manager for the whole site. Same store, same
    `purpose`, so the files tab still lists it.
    """
    entry = await committee_card(session)
    if entry is None:
        raise HTTPException(
            403,
            "You are not on this year's committee roster. If you are, ask an "
            "admin to link your email to your card.",
        )

    meta = await storage.save(file, "committee")
    row = await database.post("/uploads", {**meta, "uploaded_by": session.user.id})
    return {**row, "url": storage.public_url(row["stored_name"])}


def _own(entry: dict) -> dict:
    """One committee entry as its owner sees it: what they may change, plus the
    read-only facts ICU holds, so the form can show why a field is fixed."""
    return {
        "login": entry.get("login"),
        "cid": entry.get("cid"),
        "name": " ".join(
            p for p in [entry.get("first_name"), entry.get("last_name")] if p
        ),
        "unionRole": entry.get("post_name"),
        "displayRole": entry.get("display_role") or "",
        "intro": entry.get("intro", ""),
        "photoUrl": entry.get("photo_url"),
        "hidden": bool(entry.get("hidden")),
        "linkedEmail": entry.get("user_email"),
    }


@router.get("/admin/committee", dependencies=[Depends(require_action("committee.view"))])
async def admin_committee(session: AuthSession = Depends(require_committee_page)) -> dict:
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
                "linkedEmail": m.get("user_email") or "",
            }
            for m in members
        ],
    }
