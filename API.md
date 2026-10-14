# EESoc website — API contract

Live contract for the implemented backend. Browse the generated reference at
`http://localhost:8080/api/docs` while the stack is running.

## Services

| Service | Reachable from | Job |
|---|---|---|
| `frontend` | the host, `:8080` | Serves the SPA, proxies `/api` |
| `api` | the proxy only | Public API. **The only service that enforces authorisation** |
| `database` | `api` only | The only service that speaks SQL. Unauthenticated by design |
| `union` | `api` only | eActivities bridge — cached and throttled |
| `postgres` | `database` only | — |

## Conventions

- **Base URL:** `VITE_API_BASE` (default `/api`, same-origin via nginx — so no CORS
  and cookies just work).
- **Auth:** `session` cookie (HttpOnly, 7 days). Unsafe methods also need
  `X-CSRF-Token` matching the readable `csrf` cookie, or they 403.
- **Errors:** non-2xx return `{"detail": "..."}`.
- **Dates:** ISO timestamps, plus pre-formatted `date`/`time` display strings on
  event cards (`"Thu 22 Oct"`, `"12:00 – 17:00"`).
- 🔒 signed in · 👤 member · 🏢 sponsor · 🛠 committee/admin.

---

## Auth

**One flow for everybody.** A code proves control of an email address; membership
is then decided by looking that *verified* address up in the synced ICU feed.
There is no separate signup — an account is created on first successful
verification. Membership is never inferred from the email domain (alumni and
staff hold `@imperial.ac.uk` addresses without being members).

### `POST /auth/request-code`
```json
{ "email": "abc123@ic.ac.uk", "first_name": "Ada", "last_name": "Byron" }
```
Names are optional and only used when creating a new external account.
**Always returns 202**, whether or not the address is known — anything else would
be an oracle for "is this person an EESoc member".

Rate limited: 5 codes per address per hour, 60-second resend cooldown.

### `POST /auth/verify-code`
```json
{ "email": "abc123@ic.ac.uk", "code": "123456" }
```
Sets the session cookies and returns the session view. Codes are single-use,
expire in 10 minutes, and lock out after 5 wrong attempts.

### `GET /auth/session`
```json
{
  "authenticated": true,
  "user": { "id": 3, "email": "…", "name": "Qais Masri", "cid": "02689451", "login": "qm225" },
  "roles": ["member", "committee"],
  "is_member": true, "is_admin": true, "is_sponsor": false,
  "role": "admin",
  "capabilities": ["events", "site", "sponsors"],
  "committee_login": "qm225",
  "impersonating": false, "impersonator_email": null,
  "academic_year": "26-27"
}
```
`roles` is a **list**, not the single `role` this document used to specify — a
committee member is usually also a member, and an admin may hold a sponsor
account too.

`role` is the separate **committee** role, and is at most one of
`superuser | admin | events | industry` (or null). `capabilities` is what it
unlocks — see Access below. `committee_login` is the committee card this person
may edit, which needs no role at all.

### `POST /auth/stop-impersonating` 🔒
Ends an impersonated session and returns the caller to their own account.
Deliberately on `/auth` and not `/admin`: the session calling it is the
impersonated one, which by design holds none of the superuser's access.

### Others
`POST /auth/logout` 🔒 · `POST /auth/logout-everywhere` 🔒 · `PATCH /auth/me` 🔒
(name, `cv_opt_in`).

### Passkeys

A passkey signs somebody in with their device's own lock instead of an emailed
code — the way round a spam filter that eats our codes, and the way in while the
webmaster mailbox is down. Saved once, after a code sign-in (the login dialog
offers it to an account with none) or from `/account`. Credentials are
discoverable, so signing in needs no address: the passkey says whose it is.

Every ceremony is two calls: one returns WebAuthn options in their JSON form
(base64url fields), the next takes the browser's `PublicKeyCredential` in the
same form as `credential`. The server checks the challenge is one it issued,
unexpired and unused (reading it deletes it), for this purpose and this account
or link; py_webauthn checks origin (`SITE_ORIGIN`), relying party (its host),
signature and counter.

| Call | Who | Does |
|---|---|---|
| `POST /auth/passkey/options` → `POST /auth/passkey/verify {credential}` | anybody | sign in; returns the session view |
| `GET /auth/passkeys` | 🔒 | your passkeys: `name`, `createdAt`, `lastUsedAt`, `synced`, `viaLink`, `linkMadeBy` |
| `POST /auth/passkeys/options` → `POST /auth/passkeys {credential, name}` | 🔒 | save one on your account |
| `DELETE /auth/passkeys/{id}` | 🔒 | remove one of yours (the emailed code still works) |
| `POST /auth/passkey-link {token}` | link holder | what the link is for: `email`, `expiresAt`, `madeBy` |
| `POST /auth/passkey-link/options {token}` → `POST /auth/passkey-link/register {token, credential, name}` | link holder | save a passkey on the link's account, spend the link, sign in |

Saving or removing a passkey is refused while impersonating: a passkey outlives
the impersonation, so it would be a way back into the account with no banner.
`GET /auth/session` carries `passkeys`, the count on the account.

Signing in with a passkey is the `passkeys` maintenance feature, **not**
`login`: it needs no email, so the automatic outage when mail credentials are
missing leaves it working. Forcing `login` off by hand forces `passkeys` off too.

**Setup links** are for the people our codes never reach — usually a sponsor
behind a corporate filter. A committee member makes one (`/admin/passkey-links`,
below), sends it themselves by whatever reaches the person, and opening it lets
them save a passkey on that address's account and sign in, no code. The page is
`/passkey-setup#<token>`: the token rides in the fragment, which browsers never
send to a server, and the api takes it in POST bodies, so it is in no access
log. One link per account at a time (a new one revokes the last), single-use,
7 days (`PASSKEY_LINK_TTL_HOURS`), only its hash stored.

---

## Events

### `GET /events?upcoming=true&limit=100`
```json
{ "count": 2, "events": [
  { "id": 2, "slug": "fpga-day", "category": "Workshops", "title": "IMC FPGA Day",
    "date": "Wed 10 Mar", "time": "10:00 – 17:00", "venue": "EEE 403",
    "status": "48 places", "statusTone": "accent", "startsAt": "2027-03-10T10:00:00Z",
    "soldOut": false, "published": true } ] }
```
Admins also see drafts.

### `GET /events/{slug}`
Adds `description`, `ticketTypes[]` (with `price`, `remaining`, `soldOut`,
`membersOnly`, `selectable`) and `myReservations[]` when signed in.

Also the calendar/link fields: `mapUrl` (always — a venue map is public) and
`meetingUrl`, which the event can mark private for its meeting link
(`meetingUrlPrivate` says whether it did). A private link is returned only to
a viewer holding a live hold/confirmed booking on this event or an admin;
everybody else gets `null`. `targetYears` lists the year groups the event is
aimed at (`[1, 2]`); empty means everyone.

### `GET /events/{slug}/calendar.ics`
The event as an iCalendar file. No session — calendar clients cannot
authenticate. 404 when unpublished, the same rule as the page. `text/calendar;
charset=utf-8`, times are the stored UTC instants in Z form (an absolute time
needs no VTIMEZONE). Never contains the meeting link or any attendee data.

### `GET /events.ics` · `GET /events.ics?category=Workshops`
The subscribable feed: every published event, past ones included (a calendar
shows history), ordered by start. No auth, and the category is a plain query
param — category names are already printed on every public event card, so the
parameter leaks nothing, and a calendar client could not use an authenticated
URL anyway. The category match is case-insensitive. Same content rules as the
single-event file: no private meeting links, no drafts.

**Admin write fields** (`POST/PUT /admin/events`): `map_url`, `meeting_url`,
`meeting_url_private`, `target_years` — alongside the existing event fields.
`target_years` is a list of ints in the API (`[1, 2]`), stored as the
comma string `"1,2"`; no emails are sent from it yet, but the targeted
announcement queue will read it to aim an event's message at the right
department year-group lists.

### `POST /events/{slug}/reserve` 🔒
```json
{ "ticket_type_id": 4, "quantity": 1 }
```
Creates a **hold**, not a purchase. Returns `next.action`:
`buy` (members — matched automatically on their ICU login),
`buy_and_supply_order` (externals), or `none` (free tickets).

**409** when full, or once booking has closed (`detail`: "Booking for this event
has closed."), with the reason in `detail`. Capacity is enforced under a row lock
on the event, so concurrent requests cannot oversell — verified at 30
simultaneous bookings against a cap of 6.

**Sale windows.** An event may set `reservations_close_at` (nobody can reserve,
hold a guest seat, or join the waitlist after it; the adapter refuses too, with
reason `reservations_closed`; committee-added tickets are not gated) and
`union_sales_close_at` (no hold's pay-by deadline passes it — saving an earlier
one brings live holds forward to it). Reservations must not close after Union
sales do, and neither after the event ends (422). The event payload carries
`bookingClosesAt` and `unionSalesCloseAt`, and `salesState` is `closed` once
booking has closed.

### `GET /me/reservations` 🔒 · `POST /reservations/{id}/cancel` 🔒
### `PATCH /reservations/{id}/order-number` 🔒
```json
{ "order_number": "AB99ZZ4" }
```
How an external user proves payment. Matched case-insensitively; order numbers
contain letters. One sale can only ever confirm one reservation.

---

## Lockers

### `GET /lockers`
Grouped the way the lockers stand: level, then wall (front before side), then
blocks of nine from left to right. `row` and `col` (1–3) place a door in its
block. `number` is what is printed on the door and is **not unique** — two
blocks on the same wall can carry the same numbers — so `id` is the locker and
`place` says where it is.
```json
{ "floors": [ { "floor": 4, "note": "100 of 108 available",
                "walls": [ { "wall": "side", "label": "Side wall",
                             "blocks": [ { "block": 3, "lockers": [
                               { "id": 145, "number": 482, "row": 1, "col": 1,
                                 "place": "Level 4, side wall, 3rd block from the left, top row",
                                 "state": "free", "mine": false } ] } ] } ] } ],
  "entitlement": { "purchased": 2, "held": 1, "remaining": 1, "is_member": true } }
```
`state`: `free` | `taken` | `mine` | `inactive`. Other people's bookings never
reveal who holds them.

### `POST /lockers/{lockerId}/book` 👤
Requires a locker purchase in the synced sales feed. A member may buy several,
so entitlement is a count. Losing a race returns **409**.

### `GET /lockers/me` 🔒 · `POST /lockers/bookings/{id}/release` 🔒
`bookings`: `[ { "id", "lockerId", "number", "place", "locker", "since" } ]`,
where `locker` names it in full — `"482 (level 4, side wall, 3rd block from the
left, top row)"` — as emails and the committee's holder list do.

---

## Committee

### `GET /committee`
Names and roles come from ICU's feed; the intro paragraph and photos are ours,
joined on CID so a re-sync never clobbers an upload. Phone numbers are
deliberately not exposed.
```json
{ "intro": "Elected each spring.", "academicYear": "26-27",
  "committee": [ { "cid": "02689451", "role": "Diversity and Inclusion Officer",
                   "name": "Qais Masri", "email": "…", "intro": "…", "photoUrl": "…" } ] }
```

### `PUT /committee/intro` 🛠 · `PUT /committee/{cid}/profile` 🛠
Name and post are read-only — they belong to the Union's records.

---

## Sponsors

`GET /sponsors` · `GET /jobs` (public, live roles across all sponsors).
`GET /sponsors` carries each sponsor's `contacts` (emails and links — empty when
the committee struck the contact clause) and `events` (published events linked
to them this year). `GET /events/{slug}` carries `sponsors`: the published
sponsors linked to that event.

**Portal** 🏢 — every endpoint is scoped to the sponsors the account is linked
to, so changing an id in the URL cannot reach a competitor's data. What a
sponsor may do is bounded by their agreement (`services/agreement.py`): a clause
the committee struck is refused with 403, and countable clauses are enforced
against the allowance (marketing: 409 when used up).

| Endpoint | Purpose |
|---|---|
| `GET /portal/dashboard` | Stat tiles, sponsorable events, your events, listings, your package, unread replies |
| `GET /portal/panels/{jobs\|calendar\|request\|marketing\|contacts\|merch\|messages}` | Panel content |
| `POST /portal/jobs`, `PUT /portal/jobs/{id}` | Manage listings (`status` draft / live / closed) |
| `POST/DELETE /portal/events/{id}/interest` | Register / withdraw interest in a sponsorable event (`/claim` is the old name for POST) |
| `POST /portal/requests` | Request an event: `format` id, `body`, optional `timing` |
| `POST /portal/marketing/media` | Upload an image or PDF for a marketing request |
| `POST /portal/marketing`, `POST /portal/marketing/{id}/withdraw` | Ask EESoc to share something (subject, body, media, audiences, channels); withdraw while pending |
| `PUT /portal/contacts` | Replace the contact list: `{contacts: [{kind: email\|link, value, label, note}]}` |
| `GET /portal/notes?topic=`, `POST /portal/notes` | A thread / a question or note — `topic` is `general`, `event:ID`, `request:ID`, `marketing:ID`, `listing:ID` or `clause:KEY`. Emails the committee (`SPONSOR_NOTIFY_EMAIL`) |
| `POST /portal/merch/upload`, `DELETE /portal/merch/{id}` | Brand assets and artwork |
| `GET/PUT /portal/profile`, `POST /portal/profile/logo` | The public card |

---

## Tickets and door check-in

A ticket token is `<booking id>.<hmac>` (`services/tickets.py`); the QR encodes
`/ticket/<token>`. Own bookings on `GET /events/{slug}` carry `ticketToken`, and
every confirmation email links the ticket and shows its QR.

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /tickets/{token}` | anyone with the link | The ticket: event, holder, status, `checkedInAt`; `mayCheckIn` for committee |
| `GET /qr/{token}.png` | anyone with the link | The QR image (verified first; immutable) |
| `GET /checkin/events` | committee | Events from yesterday on, with booked / checked-in counts |
| `GET /checkin/events/{id}` | committee | The door list: attendees, counts, bookings fingerprint |
| `GET /checkin/events/{id}/state` | committee | Polled every ~2s by every door screen: all check-ins + bookings fingerprint |
| `POST /checkin/scan` | committee | `{token, event_id}` → `checked_in` / `already` / `unpaid` / `invalid` / `wrong_event` |
| `POST /checkin/reservations/{id}` | committee | Check in from the list; `override: true` for unpaid or lapsed (never another event's) |
| `DELETE /checkin/reservations/{id}` | committee | Undo a check-in |

"Committee" here is anyone on this year's roster, with or without an admin role
(`deps.require_committee`). Check-in is idempotent per booking: a second scan
reports who let them in and when.

## Admin 🛠

| Endpoint | Purpose |
|---|---|
| `GET /admin/overview` | Sync status, counts, ICU bridge health |
| `POST /admin/sync` | Pull from eActivities now, then expire + match |
| `POST /admin/reconcile` | Expire and match without calling upstream |
| `GET/POST /admin/events`, `PUT/DELETE /admin/events/{id}` | Event CRUD |
| `POST /admin/events/{id}/ticket-types`, `PUT/DELETE /admin/ticket-types/{id}` | Ticket types |
| `GET /admin/events/{id}/reservations` | Door list, split held/confirmed |
| `POST /admin/events/{id}/tickets` | Add a ticket for someone (`ticket_type_id`, `email`, names, `quantity`, `note`, `send_confirmation`) — lands **confirmed**, `source = committee`, refused when full, never expired or matched by the sync |
| `POST /admin/reservations/{id}/confirmation` | Send a confirmed booking's "you're confirmed" email again |
| `POST /admin/reservations/{id}/ticket-type` | Move a live booking to another ticket type of the same event (`{ticket_type_id}`); refused if that type is full |
| `GET /admin/union/products` | Synced product list for the link dropdown |
| `GET/POST/DELETE /admin/product-links` | **Map a Union product to a ticket type, locker or membership** |
| `GET /admin/admins` | Who holds what role (any committee role may read) |
| `POST/DELETE /admin/admins` | Grant, change or revoke a role — **superuser only**. An address with no account gets one, unverified; `neverSignedIn` flags it |
| `POST /admin/impersonate` | Open a session as another account, creating it if there is none — **superuser only** |
| `GET/POST/PUT /admin/sponsors`, `POST /admin/sponsors/{id}/users` | Sponsors and portal access |
| `GET /admin/sponsors/inbox` | Everything waiting on the committee across sponsors: notes, marketing, requests, interest, new roles |
| `GET /admin/sponsors/{id}/detail` | One sponsor's agreement, usage, events, requests, marketing, notes (opening it marks their notes read) |
| `PUT /admin/sponsors/{id}/terms/{clause}` | Agreed / allowance / note / done for one clause |
| `PUT/DELETE /admin/sponsors/{id}/events/{event_id}` | Link an existing event (with the clause it delivers) / unlink; `POST …/decline` refuses interest |
| `POST /admin/sponsors/{id}/notes` | Reply to a sponsor's thread; emails their portal users |
| `PATCH /admin/sponsor-requests/{id}` | Request status and the reply the sponsor sees |
| `PATCH /admin/sponsor-marketing/{id}`, `POST …/preview`, `…/send` (**site**), `…/posted`, `…/complete`, `…/decline` | Work a marketing request |
| `GET/POST /admin/sponsors/roles-digest` | Preview / send (**site**) one email of new roles to the "Jobs and internships" subscribers |
| `GET/PATCH /admin/lockers`, `GET /admin/locker-bookings` | Locker estate |
| `GET /admin/email/audiences` | Who can be mailed, with live counts, plus both mailboxes' state |
| `POST /admin/email/preview` | Render the message and count the audience — **sends nothing** |
| `POST /admin/email/send` | Send a newsletter |
| `POST /admin/events/{id}/announce` | Email people **about one event**, same audiences |
| `POST /admin/whatsapp/render` | The **WhatsApp version** of an announcement (`heading`, `body`, optional `event_id`, `kind`) and a `wa.me` link that opens WhatsApp with it filled in — **sends nothing**; see [WhatsApp](#whatsapp) |
| `GET /admin/email/campaigns` | Send history, updated live while one runs |
| `GET /admin/events/{id}/sale-alerts` | Who is waiting to hear this event is on sale |
| `GET /admin/email/optouts` | Who has unsubscribed (read-only, deliberately) |
| `POST /admin/email/announce-now` | Run the last-chance reminder pass now instead of on the hour |
| `GET /admin/email/queue` | Bulk email the site wrote on its own: **waiting for approval**, or **scheduled** (approved for its `sendAt`) — nothing in it has been sent |
| `GET /admin/email/queue/{id}` | One email: words, ticked audiences, hand-added/removed addresses, `sendAt`, the resolved recipient list and a preview of the whole message |
| `PUT /admin/email/queue/{id}` | Edit `subject`, `heading`, `body`, `audiences`, `extra` (addresses added), `excluded` (addresses taken off) — only while waiting for approval; answers with it re-resolved and re-rendered |
| `POST /admin/email/queue/{id}/test` | Send it as it stands to the Test list only, subject prefixed `[Test]` |
| `POST /admin/email/queue/{id}/send` | Send now, waiting or scheduled: recipients resolved again, then the ordinary bulk send. A second press is a 409 |
| `POST /admin/email/queue/{id}/schedule` | Approve it for its `sendAt`; it then goes by itself at that moment. Refused if the time has passed |
| `POST /admin/email/queue/{id}/unschedule` | Take an approval back — it waits for approval again, editable |
| `POST /admin/email/queue/{id}/dismiss` | Decide it should not go. Nothing is sent and it does not come back |

**Product links** are what tie a Union purchase to a booking. Leaving
`union_product_line_id` null means "any line of this product", which is the safe
default — line ids are not documented as unique across products.

**First admin:** with no admins in the database, the first *committee member*
(per the ICU feed) to sign in is promoted to superuser automatically, then the
rule stops. Superuser rather than admin: an admin cannot grant roles, so on a
fresh deployment that would be a committee with the keys locked inside.

---

## Access 🔒

One role per account, in `admins.role`. The table of what each unlocks is
`backend/api/utils/roles.py`; enforcement is `backend/api/utils/deps.py`.

| Role | users | events | sponsors | site | passkey_links |
|---|:--:|:--:|:--:|:--:|:--:|
| `superuser` | ✓ | all | ✓ | ✓ | ✓ |
| `admin` | — | all | ✓ | ✓ | ✓ |
| `events` | — | all | — | — | — |
| `industry` | — | industry events only | ✓ | — | — |

* **users** — grant and revoke roles, and impersonate. Superuser only.
* **events** — events, ticket types, bookings, waitlists, product links on an
  event. `industry` is the same set, narrowed to events with `industry = true`:
  the careers fair, a sponsor's talk, a site visit. An event created by somebody
  holding only `industry` is flagged automatically, so they cannot make one they
  are then locked out of.
* **sponsors** — sponsors, their listings and requests, and **adding emails to a
  sponsor portal**. That last one is on `sponsors` and not `users` on purpose:
  it creates an account that can see one company's portal and nothing else,
  which is sponsorship work rather than access control.
* **site** — resources, files, lockers, Union products, bulk email, the
  committee page's layout and other people's cards.
* **passkey_links** — make, list and revoke passkey setup links
  (`GET/POST /admin/passkey-links {email}`, `DELETE /admin/passkey-links/{id}`).
  A link lets whoever holds it into the account, so: never for the standing
  superuser; for an account holding **any** committee role, only with `users`
  too (otherwise an admin could link a superuser's account and walk in); not
  while impersonating. The same rule is checked again when the link is *used* —
  a link is honoured only if its maker could still make it, so promoting the
  account or demoting the maker afterwards kills it. An unknown address gets an
  unverified account, as with sponsor access. The URL is returned once, in the
  response; nothing is emailed. Making, using and revoking are logged at
  WARNING with both addresses.

**Editing your own committee card needs no role.** Anyone matched to a row on
this year's roster can change their own intro and photograph
(`GET/PUT /committee/me`, `POST /committee/me/photo`) and nothing else.
Matching is by linked address, then login/shortcode, then the address ICU holds.
Where none of those reach the right account, an admin sets `user_email` on the
profile (`PUT /committee/{login}/profile`) and that address owns the card.

**The standing superuser** is `SUPERUSER_EMAIL` (default `ras124@ic.ac.uk`).
That address is a superuser on every request whatever the database says, cannot
be demoted or removed through the API, and is recorded in `admins` the first
time it signs in. Changing it is a deploy, which is the point.

**Impersonation** mints a real session for the target account with
`sessions.impersonator_id` set to the superuser. A red banner shows on every
page until it ends, nesting is refused, the standing superuser cannot be
impersonated, and both the start and the end are logged at WARNING with both
addresses.

---

## Email

Two mailboxes, because they fail differently and must not take each other down:

| Profile | Mailbox | Carries |
|---|---|---|
| transactional | `SMTP_*` — webmaster | Sign-in codes, your own booking, your own locker |
| publicity | `PUB_SMTP_*` — publicity officer | Newsletters, announcements, on-sale alerts |

Blank `PUB_SMTP_*` fields fall back to their `SMTP_*` counterparts. Blank
`PUB_SMTP_USER`/`PUB_SMTP_PASS` specifically means "log in as the webmaster
account, send **from** the publicity address" — the normal Exchange arrangement
for a shared mailbox, which needs Send As granted on it. If Exchange refuses the
sender the message is retried once from the webmaster address rather than lost,
and the problem is logged.

Check either with `python3 scripts/check-smtp.py [--pub]`.

### What gets sent, and when

| Trigger | Email | Profile |
|---|---|---|
| A hold is taken | "We're holding your ticket" — with the Union purchase link and the pay-by deadline | transactional |
| ~6h before a hold lapses unpaid (never within 2h of booking) | "Last chance" | transactional |
| A seat frees on a waitlist | The offer, with its deadline | transactional |
| An event is published with a sale time | "Tickets are on sale", to everyone who asked about the event and the subscribers — **drafted into the queue at once, and sent when sales open once a committee member has approved it** (or sooner, if they press Send now) | publicity |
| Committee presses send | Newsletter, or a message about one event | publicity |

**Nothing addressed to a crowd goes without a person's approval.** Mail about one
person's own booking or sign-in (the first three rows, the booking confirmation,
the sign-in code) goes straight to them. Anything else the site writes on its
own waits in `GET /admin/email/queue` until somebody with the `site` capability
edits, tests, and sends it now, approves it for its time, or dismisses it; set
`EMAIL_APPROVAL_NOTIFY` (comma separated) to have those people emailed when
something arrives there. Every bulk send — queued or pressed — is one message per
batch with the recipients BCC'd.

The on-sale draft follows its event: written when the event is saved published
with a sale time, rewritten when the event changes (unless somebody has edited
it), its send time moved when the sale moves, and taken back off the schedule if
the event is unpublished or deleted — never sent on a stale approval. An approved
email goes **at its time**: the sender sleeps until the next one is due rather
than polling.

Recurring jobs run **hourly, on the hour** — never more often. The last-chance
reminder pass is one: it reads **only our own database**, so it costs nothing
against the Union rate limit, and it *claims* its work in SQL before sending, so a
crash mid-pass loses an email rather than sending it twice — the right way
round, because somebody mailed twice unsubscribes.

### Where the purchase link comes from

Most specific first, because the Union's product feed often carries no URL at
all for our centre:

1. `ticket_types.purchase_url` — set by the committee
2. the URL on the Union product that ticket type is linked to
3. `events.purchase_url` — set by the committee
4. the URL on the Union product linked to the event

None of them? The email says "find it on the Union shop" rather than linking to
a page that does not exist.

### Public endpoints

| Endpoint | Purpose |
|---|---|
| `GET /email/topics` | Subscribable topics; includes the caller's choices when signed in |
| `PUT /me/email-preferences` 🔒 | Save topic choices |
| `GET /me/email-status` 🔒 | Whether this address is suppressed outright |
| `POST /events/{slug}/notify-me` 👤 | "Tell me when this goes on sale" — **no account needed** |
| `DELETE /events/{slug}/notify-me` 🔒 | Cancel that |
| `GET /email/unsubscribe?e=&t=` | What a link is about to do |
| `POST /email/unsubscribe?e=&t=` | Unsubscribe — **unauthenticated by design** |
| `POST /email/unsubscribe/request` | Email an address its own signed unsubscribe link |
| `POST /me/email-resubscribe` 🔒 | Undo a blanket unsubscribe (needs a session) |

Unsubscribing is deliberately open: the request arrives from a mail client, days
later, from somebody who may never have signed in, and an unsubscribe that asks
you to log in first is one people answer with the spam button. What stands in
for authentication is an HMAC over the address (`EMAIL_TOKEN_SECRET`) — enough
that one person cannot unsubscribe another. Re-subscribing is **not** open, or a
forwarded email would let anyone put an address back on a list.

Bulk mail is **BCC'd in batches** (`BULK_BATCH_SIZE`, default 400) rather than
sent one message per person, so a newsletter to a few hundred goes out in
seconds. The visible To is the publicity mailbox; nobody sees anybody else's
address. The price is the per-person unsubscribe link: a shared message carries
the shared `/unsubscribe` page, which emails anybody their own signed link on
request (`POST /email/unsubscribe/request`, from the webmaster mailbox, throttled
and answering the same whether or not the address is known). Mail with something
personal in it — a sign-in code, a booking, a per-event "tell me" alert — is
still sent to that person alone, with their own link.

Every bulk message carries `List-Unsubscribe`; only one carrying a signed link
also claims `List-Unsubscribe-Post` (one-click), because POSTing to the shared
page unsubscribes nobody. Header folding is turned off when building messages
precisely so that header survives intact — folded by the default policy it
becomes an RFC 2047 encoded-word, which clients silently ignore.

### Who can be mailed

- **members** — everyone ICU reports as a member this year, from the synced
  feed. Most have never signed in; they are who a society newsletter is for.
- **accounts** — everyone with an account on the site.
- **topic:`<key>`** — standing subscribers. An account with no row counts as the
  topic's declared default (`newsletter`, `events_new` and `tickets_on_sale` are
  on by default; everything else is opt-in).
- **department lists** — the year-group aliases (`ee1ugs@ic.ac.uk` and the
  rest). One address each, owned by the department — nothing here can
  unsubscribe them, and they are listed separately for that reason.

Addresses are de-duplicated across every audience, and opt-outs are applied in
SQL rather than by the caller, so a new audience cannot forget them.

### WhatsApp

The site writes WhatsApp posts; a committee member sends them. Every place an
announcement is written — the newsletter composer, an event's **Announce**
dialog, a draft in the email queue — has a **WhatsApp version**: the same
words in WhatsApp's formatting (`*bold*`, `- ` lists, links as `label (url)`),
with an event's date, venue, doors-close, prices and link added from the
database by the same helpers the email uses. **Open in WhatsApp** is a
`https://wa.me/?text=…` link: WhatsApp opens with the post filled in, they
choose the community or group, and press send. For a channel, Copy and paste.

The site never posts by itself, on purpose. WhatsApp's Business API cannot post
into a group or community — only to individual opted-in numbers, as
pre-approved templates, paid per message — and automating WhatsApp Web breaks
WhatsApp's terms, with the ban landing on the number that runs the society's
community. A person pressing send is also the approval that
"nothing addressed to a crowd goes without a person's approval" requires.

`POST /admin/whatsapp/render` reads only our own database. It warns, without
refusing, when the event is unpublished (the link would not open for anybody
else), has started, or — for an on-sale post — its sales have not opened yet.

---

## How booking actually works

```
reserve ──> held ──(matching Union sale found)──> confirmed
              │
              ├──(hold window elapses)──────────> expired
              └──(user or admin cancels)────────> cancelled
```

eActivities refreshes online sales **once a day at 06:00**, so the api service
syncs at 06:15. A hold on a ticket linked to a Union product lasts whole days
and ends at **midnight London time**: `hold_hours` rounded up to days (at least
one), ending 23:59:59 on the booking day plus days − 1 — a day later if booked
at 23:00 or after — and never after `union_sales_close_at`. A purchase made
before midnight is matched by the next morning's sync, which confirms a held
booking even once its deadline has passed. A hold on an unlinked ticket lasts
until the event, as nothing but the committee can confirm it. The rule lives in
SQL (`HOLD_EXPIRES`) with a tested Python mirror in `api/utils/holds.py`.

Matching:
- **Members** — on `customer_login` / `customer_cid` from the sale. Nothing to do.
- **Externals** — on the order number they type in.

The eActivities API is rate limited (excessive requests earn a 5-minute IP ban,
repeated auth failures an hour), so **nothing in a request path calls it**. The
bridge throttles and caches; every read serves from the local mirror.

---

## Configuration

`.env` (compose interpolation) holds Postgres credentials. `backend.env` (app
secrets) holds `ICU_API_KEY`, `ICU_CENTRE_CODE`, the `SMTP_*` and `PUB_SMTP_*`
settings and `EMAIL_TOKEN_SECRET`. See `.env.example` and `backend.env.example`.

With no `SMTP_HOST` set, login codes are **written to the api log** instead of
emailed, so the flow is testable before credentials exist.

Passkeys are bound to the host of `SITE_ORIGIN` (override with
`WEBAUTHN_RP_ID`; extra allowed origins in `WEBAUTHN_EXTRA_ORIGINS`). A passkey
made on one host never works on another — production's are useless on staging,
which is why staging's `SITE_ORIGIN` must stay its own — and **changing the
production domain strands every passkey on it**: people fall back to emailed
codes and save new ones. `PASSKEY_CHALLENGE_SECONDS` (300) and
`PASSKEY_LINK_TTL_HOURS` (168) bound the ceremonies and the setup links.
