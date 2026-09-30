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
  "academic_year": "26-27"
}
```
`roles` is a **list**, not the single `role` this document used to specify — a
committee member is usually also a member, and an admin may hold a sponsor
account too.

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

**Portal** 🏢 — every endpoint is scoped to the sponsors the account is linked
to, so changing an id in the URL cannot reach a competitor's data:

| Endpoint | Purpose |
|---|---|
| `GET /portal/dashboard` | Stat tiles, claimable events, your events, listings |
| `GET /portal/panels/{jobs\|calendar\|request\|merch\|cv}` | Panel content |
| `POST /portal/jobs`, `PUT /portal/jobs/{id}` | Manage listings |
| `POST /portal/events/{id}/claim` | Claim a sponsorable slot |
| `POST /portal/requests` | Request an event format |
| `POST /portal/merch/upload` | Record artwork **by URL** (no object store wired up) |
| `GET /portal/cv-book` | Opt-in CV book |

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
| `GET /admin/union/products` | Synced product list for the link dropdown |
| `GET/POST/DELETE /admin/product-links` | **Map a Union product to a ticket type, locker or membership** |
| `GET/POST/DELETE /admin/admins` | Manage committee access |
| `GET/POST/PUT /admin/sponsors`, `POST /admin/sponsors/{id}/users` | Sponsors and portal access |
| `GET/PATCH /admin/lockers`, `GET /admin/locker-bookings` | Locker estate |

**Product links** are what tie a Union purchase to a booking. Leaving
`union_product_line_id` null means "any line of this product", which is the safe
default — line ids are not documented as unique across products.

**First admin:** with no admins in the database, the first *committee member*
(per the ICU feed) to sign in is promoted automatically, then the rule stops.

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
secrets) holds `ICU_API_KEY`, `ICU_CENTRE_CODE` and the `SMTP_*` settings. See
`.env.example` and `backend.env.example`.

With no `SMTP_HOST` set, login codes are **written to the api log** instead of
emailed, so the flow is testable before credentials exist.
