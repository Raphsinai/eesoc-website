"""Announcements as WhatsApp posts.

What is posted is the committee's words in WhatsApp's own formatting, with an
event's date, venue, prices and link added from the database exactly as the
email adds them — so a WhatsApp reminder cannot advertise last week's room
either. It reaches the society's groups one of two ways:

  * by hand — "Open in WhatsApp" is a wa.me link that opens the app with the
    text filled in, and a committee member picks the group and presses send;
  * by the site — the society's WhatsApp Business number is linked to the site
    as one of its devices (backend/whatsapp). Every group it can post in is
    an audience like any other, ticked under "Who gets it" (or in the picker
    on a send without that list) and posted into by the same Send — now, or
    with an email at the time it was approved for.

Either way a person said yes to every post, as for every email to a crowd.

The linked device is NOT the WhatsApp Business API, which cannot post into a
group it did not create; it is the WhatsApp Web protocol, which WhatsApp's
terms do not allow for automation. If WhatsApp decides it is a bot, the number
that runs the society's community is banned, with no appeal — the eActivities
hazard over again. backend/whatsapp/src/link.js says how it keeps that risk
small; this file posts only where a person ticked, records every post before
it is made so none goes twice, and honours WHATSAPP_GROUP_ALLOWLIST as a hard
fence where it is set.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import HTTPException

from clients import database
from clients import whatsapp as link
from config import settings
from services import campaigns, emailing, purchase

# The email body's own patterns, so a construct is recognised in both or in
# neither: what the committee types once must mean the same in each place.
from services.emailing import _BOLD, _HEADING, _LINK
from utils.present import format_doors_close


def _bold(text: str) -> str:
    """WhatsApp's *bold*, which only takes when the stars touch the words."""
    text = " ".join(_BOLD.sub(r"\1", text).split())
    return f"*{text}*" if text else ""


def _link(match: re.Match) -> str:
    label, href = match.group(1), match.group(2)
    if href.startswith("/"):
        href = f"{settings.site_origin}{href}"
    elif not re.match(r"^(https?://|mailto:)", href, re.I):
        # The email drops these to their label too: neither a `javascript:`
        # nor a bare word is somewhere a member can be sent.
        return label
    # WhatsApp makes a bare address tappable; "mailto:" in front is noise.
    href = re.sub(r"^mailto:", "", href, flags=re.I)
    # WhatsApp has no link text — it shows the address and makes it tappable —
    # so the label stays and the address follows it, as in the text email.
    return label if label.strip() == href.strip() else f"{label} ({href})"


def _inline(text: str) -> str:
    return _BOLD.sub(r"*\1*", _LINK.sub(_link, text))


def from_markdown(markdown: str) -> str:
    """The committee's markdown as WhatsApp formats it.

    The same blocks as `emailing.render_body`: a heading becomes a bold line,
    a block of bullets stays a block of bullets — WhatsApp draws "- " as a
    list, and "* " would read as the start of something bold — and a single
    newline inside a paragraph stays a line break.
    """
    out: list[str] = []
    for block in re.split(r"\n{2,}", (markdown or "").strip()):
        block = block.strip()
        if not block:
            continue

        heading = _HEADING.match(block)
        if heading and "\n" not in block:
            out.append(_bold(_LINK.sub(_link, heading.group(2))))
            continue

        lines = [line.strip() for line in block.split("\n")]
        if all(line.startswith(("- ", "* ")) for line in lines):
            out.append("\n".join(f"- {_inline(line[2:])}" for line in lines))
            continue

        out.append("\n".join(_inline(line) for line in lines))
    return "\n\n".join(out)


async def message(
    heading: str, body: str, event: dict | None = None, kind: str | None = None
) -> str:
    """The whole post: a bold first line, the committee's words, and — for an
    event — its details and link, from the same helpers the email uses.

    `kind` is a campaign kind; only "on_sale" changes anything: the standard
    opening when none is written, and the Union shop link, as in that email.
    """
    heading = " ".join((heading or "").split())
    parts = [_bold(heading)] if heading else []
    opening = from_markdown(body)

    if event is None:
        return "\n\n".join(parts + ([opening] if opening else []))

    title = event["title"]
    if not opening and kind == "on_sale":
        opening = f"Tickets for *{title}* are on sale now."
    if opening:
        parts.append(opening)

    details: list[str] = []
    # Named once: a title already in the heading or the words is not repeated
    # above the details, which on a phone is a line of scrolling for nothing.
    if title.casefold() not in f"{heading} {opening}".casefold():
        details.append(_bold(title))
    details.append(f"*When:* {campaigns.event_when(event)}")
    if event.get("venue"):
        details.append(f"*Where:* {event['venue']}")
    doors = format_doors_close(event.get("starts_at"), event.get("doors_close_at"))
    if doors:
        details.append(f"*Doors close:* {doors}")
    tickets = campaigns.event_tickets(event)
    if len(tickets) == 1:
        details.append(f"*Tickets:* {tickets[0][0]} — {tickets[0][1]}")
    elif tickets:
        details.append("*Tickets:*\n" + "\n".join(f"- {n} — {p}" for n, p in tickets))
    parts.append("\n".join(details))

    url = f"{settings.site_origin}/events/{event['slug']}"
    if kind == "on_sale":
        links = [f"Reserve your place: {url}"]
        shop = await purchase.link_for(event)
        if shop:
            links.append(f"Buy on the Union shop: {shop}")
        parts.append("\n".join(links))
    else:
        parts.append(f"Full details and booking: {url}")
    return "\n\n".join(parts)


def warning(event: dict | None, kind: str | None = None) -> str | None:
    """Why this should not be posted yet, if anything.

    Shown, not enforced, as for a queued email — but it matters more here:
    a message in a group of hundreds cannot be quietly corrected afterwards.
    """
    if event is None:
        return None
    if not event.get("published"):
        return (
            "This event isn't published, so the link in the post won't open "
            "for anybody else yet."
        )
    now = campaigns.utcnow()
    starts = campaigns.parse_time(event.get("starts_at"))
    if starts is not None and starts <= now:
        return "This event has already started."
    opens = campaigns.parse_time(event.get("sales_open_at"))
    if kind == "on_sale" and opens is not None and opens > now:
        return (
            f"Sales don't open until {campaigns.when_text(opens)}. Post this then "
            "— before that, it says tickets are on sale when they aren't."
        )
    return None


def share_url(text: str) -> str:
    """wa.me with no number: WhatsApp opens with the text filled in and asks
    which chat it is for — the app on a phone, WhatsApp Desktop or Web on a
    laptop."""
    return f"https://wa.me/?text={quote(text, safe='')}"


# ------------------------------------------------------------ the linked device

log = logging.getLogger("api.whatsapp")

# A second press of "Post" inside this long, with the same words, into the
# same group, is taken for a double press and refused.
REPEAT_MINUTES = 5


def allowlist() -> set[str] | None:
    """A hard fence on where this environment may post, or None for none.

    Optional, in any environment. Set WHATSAPP_GROUP_ALLOWLIST and nothing
    outside it is offered or posted to, whatever is ticked.
    """
    listed = {j.strip() for j in settings.whatsapp_group_allowlist.split(",") if j.strip()}
    return listed or None


def allowed(jid: str) -> bool:
    groups = allowlist()
    return groups is None or jid in groups


def label(group: dict) -> str:
    """What a group is called where it is ticked. A community's announcements
    channel takes the community's name, so it says what it is — one place a
    post can go, beside the community's groups, never a stand-in for them."""
    if group.get("communityAnnouncements"):
        return f"Announcements — {(group.get('parent') or {}).get('name') or group.get('name')}"
    return group.get("name") or group["jid"]


async def inventory(account: str = 'eesoc') -> list[dict]:
    """Live groups for configuration. Only call from a permission-checked route."""
    if not link.configured(): return []
    try:
        rows=await link.groups(account)
    except link.WhatsAppUnavailable:
        return []
    return [{
        'jid':g['jid'],'name':label(g),'size':g.get('size'),
        'community':(g.get('parent') or {}).get('name'),
        'announcements':bool(g.get('communityAnnouncements')),
        'canPost':bool(g.get('canPost')),'account':account,
    } for g in rows if not g.get('community')]


async def available(*, user_id: int | None = None, admin_all: bool = False,
                    society_id: int | None = None) -> list[dict]:
    """Only enabled groups in the caller's tenancy and group-specific ACL."""
    if not link.configured() or (user_id is None and society_id is None and not admin_all):
        return []
    settings_rows=await database.get('/external-societies/whatsapp/groups/settings') or []
    eligible=[]
    for row in settings_rows:
        if not row['enabled']: continue
        if society_id is not None:
            if row['society_id'] != society_id: continue
        elif not (row['eesoc_enabled'] and user_id in row['eesoc_user_ids']):
            continue
        eligible.append(row)
    accounts={r['account_key'] for r in eligible}
    live={a:{g['jid']:g for g in await inventory(a)} for a in accounts}
    groups=[]
    for item in eligible:
        account=item['account_key']
        g=live.get(account,{}).get(item['jid'])
        if not g or not g['canPost'] or (account=='eesoc' and not allowed(g['jid'])):
            continue
        target=(f'{account}:' if account!='eesoc' else '')+g['jid']
        groups.append({**g,'target':target, 'society_id':item['society_id'], 'scope':'society' if society_id is not None else 'eesoc'})
    groups.sort(key=lambda g:(not g['announcements'],g['name'].casefold()))
    return groups


async def pick(jids: list[str] | None, *, user_id: int | None = None,
               admin_all: bool = False, society_id: int | None = None) -> list[dict]:
    """Never trust browser-supplied JIDs. Resolve against the current group ACL."""
    live={g['target']:g for g in await available(
        user_id=user_id,admin_all=admin_all,society_id=society_id)}
    return [live[j] for j in dict.fromkeys(jids or []) if j in live]


async def text_for(row: dict) -> str:
    """A queued email's WhatsApp version: its heading (or subject), its words,
    and its event's details — the same post "WhatsApp version" shows."""
    event = None
    if row.get("kind") in campaigns.EVENT_KINDS and row.get("event_id") is not None:
        event = await database.get(f"/events/{row['event_id']}")
    return await message(
        row.get("heading") or row.get("subject") or "", row.get("body") or "",
        event, row.get("kind"),
    )


async def _finish(post_id: int, outcome: dict) -> None:
    """Record how a post went. Never raises: by now the post has gone (or
    failed) whatever the record says, and the next group must still get its
    turn. A record left at "sending" reads as "may have gone" — the safe way
    round, since that is what stops it going again."""
    try:
        await database.patch(f"/whatsapp/posts/{post_id}", json=outcome)
    except Exception:  # noqa: BLE001
        log.exception("Could not record WhatsApp post %s as %s", post_id, outcome.get("status"))


async def _eesoc_post_granted(user_id:int|None) -> bool:
    """Resolve active EESoc action grants when a queued post is dispatched.

    Group-specific access alone must not survive the loss of a committee role
    or a role-default change. This also covers a queued email sent much later.
    """
    if user_id is None:
        return False
    from utils import roles
    rows=await database.get('/auth/admins') or []
    actor=next((r for r in rows if r.get('user_id')==user_id),None)
    if not actor:
        return False
    if settings.superuser_email and (actor.get('email') or '').lower()==settings.superuser_email.lower():
        return True
    presets=await database.get('/committee-roles') or []
    preset=next((p for p in presets if p.get('id')==actor.get('role')),None)
    effective=roles.effective_capabilities(
        actor.get('role'), actor.get('permissions') if actor.get('permissions') is not None
        else (preset or {}).get('capabilities',[]))
    return 'whatsapp.post' in effective


async def post(
    text: str,
    groups: list[dict],
    *,
    campaign_id: int | None = None,
    by: int | None = None,
) -> list[dict]:
    """Post `text` into each group, recording each post before it is made.

    With a `campaign_id`, the record is the claim: a group this email has
    already gone to — from another pod's scheduler, or a retry — is skipped,
    never posted twice. Without one (somebody pressed "Post"), the same words
    in the same group within REPEAT_MINUTES are taken for a double press.

    One group's failure does not stop the next. Returns what happened in each.
    """
    results = []
    for group in groups:
        jid, name = group["jid"], group.get("name") or ""
        account=group.get('account','eesoc')
        result = {"jid": jid, "name": name}
        settings_rows=await database.get('/external-societies/whatsapp/groups/settings') or []
        current=next((r for r in settings_rows if r['account_key']==account and r['jid']==jid),None)
        approved=bool(current and current['enabled'])
        if group.get('scope')=='society':
            approved=approved and current['society_id'] is not None and current['society_id']==group.get('society_id')
        elif group.get('scope')=='eesoc':
            approved=approved and current['eesoc_enabled'] and by in current['eesoc_user_ids'] and await _eesoc_post_granted(by)
        else:
            approved=False
        if not approved or (account=='eesoc' and not allowed(jid)):

            results.append({**result, "status": "skipped",
                            "error": "Group is disabled or outside this environment allowlist."})
            continue
        if campaign_id is None:
            recent = await database.get(
                "/whatsapp/posts",
                {"jid": jid, "within_minutes": REPEAT_MINUTES, "limit": 5},
            ) or []
            if any(p.get("body") == text and p.get("status") != "failed" for p in recent):
                results.append({**result, "status": "skipped",
                                "error": "Just posted there — not posting it twice."})
                continue
        try:
            claim = await database.post("/whatsapp/posts", {
                "campaign_id": campaign_id, "jid": jid, "group_name": name,
                "body": text, "created_by": by,
            })
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            results.append({**result, "status": "skipped", "error": "Already posted with this email."})
            continue
        try:
            sent = await link.send(jid, text, key=f"post:{claim['id']}", account=account)
        except link.WhatsAppUnavailable as exc:
            await _finish(claim["id"], {"status": "failed", "error": str(exc)})
            log.warning("WhatsApp post %s to %s failed: %s", claim["id"], jid, exc)
            results.append({**result, "status": "failed", "error": str(exc)})
            continue
        await _finish(claim["id"], {"status": "sent", "message_id": sent.get("id")})
        results.append({**result, "status": "sent", "error": None})
    log.info("WhatsApp: %s", ", ".join(f"{r['name'] or r['jid']}={r['status']}" for r in results))
    return results


def groups_of(row: dict) -> list[str]:
    """The WhatsApp groups an email goes to: the `whatsapp:` keys among its
    audiences, ticked in the same list as everyone else it goes to.

    (079's `whatsapp_groups` column held them for an hour before that, on
    staging only; the snapshot refresh after the next deploy emptied it, and
    nothing reads it now.)"""
    return emailing.whatsapp_groups_in(row.get("audiences"))


async def post_campaign(row: dict) -> list[dict]:
    """Post an email's WhatsApp version into its WhatsApp groups, if it has
    any. Called as the email starts sending. Never raises: the email has gone
    whatever WhatsApp does, and what happened is on the posts list for the
    committee to see."""
    jids = groups_of(row)
    if not jids:
        return []
    try:
        groups = await pick(jids,user_id=row.get("reviewed_by") or row.get("created_by"))
        if not groups:
            log.warning("Email %s was to go on WhatsApp, but none of its groups can be posted in now", row["id"])
            return []
        return await post(await text_for(row), groups,
                          campaign_id=row["id"], by=row.get("reviewed_by"))
    except Exception:  # noqa: BLE001
        log.exception("Could not post email %s on WhatsApp", row.get("id"))
        return []
