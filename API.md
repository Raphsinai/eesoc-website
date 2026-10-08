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

### `POST /events/{slug}/reserve` 🔒
```json
{ "ticket_type_id": 4, "quantity": 1 }
```
Creates a **hold**, not a purchase. Returns `next.action`:
`buy` (members — matched automatically on their ICU login),
`buy_and_supply_order` (externals), or `none` (free tickets).

**409** when full, with the reason in `detail`. Capacity is enforced under a row
lock on the event, so concurrent requests cannot oversell — verified at 30
simultaneous bookings against a cap of 6.

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
```json
{ "banks": [ { "letter": "A", "note": "10 of 12 free",
               "lockers": [ { "id": "A-1", "lockerId": 1, "state": "free", "mine": false } ] } ],
  "entitlement": { "purchased": 2, "held": 1, "remaining": 1, "is_member": true } }
```
`state`: `free` | `taken` | `mine` | `inactive`. Other people's bookings never
reveal who holds them.

### `POST /lockers/{lockerId}/book` 👤
Requires a locker purchase in the synced sales feed. A member may buy several,
so entitlement is a count. Losing a race returns **409**.

### `GET /lockers/me` 🔒 · `POST /lockers/bookings/{id}/release` 🔒

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

## Admin 🛠

| Endpoint | Purpose |
|---|---|
| `GET /admin/overview` | Sync status, counts, ICU bridge health |
| `POST /admin/sync` | Pull from eActivities now, then expire + match |
| `POST /admin/reconcile` | Expire and match without calling upstream |
| `GET/POST /admin/events`, `PUT/DELETE /admin/events/{id}` | Event CRUD |
| `POST /admin/events/{id}/ticket-types`, `PUT/DELETE /admin/ticket-types/{id}` | Ticket types |
| `GET /admin/events/{id}/reservations` | Door list, split held/confirmed |
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
| `GET /admin/email/campaigns` | Send history, updated live while one runs |
| `GET /admin/events/{id}/sale-alerts` | Who is waiting to hear this event is on sale |
| `GET /admin/email/optouts` | Who has unsubscribed (read-only, deliberately) |
| `POST /admin/email/announce-now` | Run the on-sale + last-chance pass immediately |

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

| Role | users | events | sponsors | site |
|---|:--:|:--:|:--:|:--:|
| `superuser` | ✓ | all | ✓ | ✓ |
| `admin` | — | all | ✓ | ✓ |
| `events` | — | all | — | — |
| `industry` | — | industry events only | ✓ | — |

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
| ~24h before a hold lapses unpaid | "Last chance" | transactional |
| A seat frees on a waitlist | The offer, with its deadline | transactional |
| An event's `sales_open_at` passes | "Tickets are on sale" to everyone who asked | publicity |
| Committee presses send | Newsletter, or a message about one event | publicity |

The on-sale and last-chance jobs run every `ANNOUNCE_INTERVAL_MINUTES` (default
10). Both read **only our own database**, so they cost nothing against the Union
rate limit. Both *claim* their work in SQL before sending, so a crash mid-send
loses an email rather than sending it twice — the right way round, because
somebody mailed twice unsubscribes.

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

---

## How booking actually works

```
reserve ──> held ──(matching Union sale found)──> confirmed
              │
              ├──(hold window elapses)──────────> expired
              └──(user or admin cancels)────────> cancelled
```

eActivities refreshes online sales **once a day at 06:00**, so the api service
syncs at 06:15 and holds last `hold_hours` (default 72, minimum 24 — anything
less would expire bookings that had been paid for).

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
