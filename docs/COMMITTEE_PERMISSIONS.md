# Committee access: role presets and individual permissions

The committee admin **People** page lets an authorised access manager choose a
role and then tick/untick individual permissions. Role selection preselects
that role's permissions. Each checkbox works independently of the role label.
**Save access** updates both in the same backend request; only a user with the
`users` permission can save changes. Standing superuser access cannot be edited.

## Presets (defaults, not access-control boundaries)

| Role | Initially selected permissions |
| --- | --- |
| Superuser | All permissions |
| Admin | All except `users`, `locker_allowances` |
| Events | `events`, `bookings`, `sync` |
| Industry | `industry`, `industry_bookings`, `sponsors` |

The live permission catalogue and descriptions are maintained in
`backend/api/utils/roles.py`. The UI uses the `permissions` list returned by
`GET /admin/admins` so labels and valid keys remain in sync.

| Permission | Scope |
| --- | --- |
| `users` | Grant/revoke committee roles and impersonate accounts |
| `passkey_links` | Issue/revoke recovery links; issuing for committee accounts also requires `users` |
| `events` | Create and edit all events, tickets, product associations |
| `industry` | Create and edit industry-marked events only |
| `bookings` | See and manage reservations, tickets and waitlists across events |
| `industry_bookings` | Reservations and waitlists of industry-marked events only |
| `sync` | Run Union sales/membership sync and booking reconciliation |
| `sponsors` | Sponsor profiles, agreements, requests, portal and listings |
| `resources` | Unpublished resource visibility and editing/import |
| `files` | Shared media upload, browse and removal |
| `committee` | Committee page administration and roster sync |
| `product_links` | Union product links for lockers and membership |
| `lockers` | Inventory, holders, and year-end locker maintenance |
| `locker_allowances` | Complimentary locker entitlements |
| `email` | Email composition, approvals, sending and audience data |
| `whatsapp` | WhatsApp linking/groups and posting to groups |
| `opportunities` | Private opportunity moderation and reviewer alerts |

Email/WhatsApp combined sends check **both** permissions when both channels
are selected. Scheduled email approval that includes WhatsApp likewise needs
both. Editing events does not implicitly grant access to attendee lists, and
editing resources does not grant unrelated file uploads.

## Storage and compatibility

Migration `083_fine_grained_permissions.sql` adds `public.admins.permissions
TEXT[] DEFAULT NULL`.

* `NULL`: follow the role's current preset; all existing records preserve
  their previous effective grants on first upgrade.
* `[]`: explicitly no permissions, but retains the role label and read-only
  admin-area access.
* `['events', 'files', ...]`: exactly these grants, whether or not the role's
  preset contains them. A saved custom selection is **not** unioned with the
  role preset.

The API validates every named permission and rejects duplicates or unknown
entries. Request-session resolution pulls the database grants afresh; browser
visibility is only a convenience, never the authorization control. Configured
standing superuser permissions are pinned in server-side session resolution.
Self-editing is refused when it would remove the requester's own `users` grant.

The API stores grants by sending a JSON body
`{"email": "person@example.org", "role": "events", "permissions": ["bookings"]}`
to `POST /admin/admins`; `permissions: null` reverts to role defaults. Changes
require CSRF protection. The database adapter must remain internal to the
Compose network; **never expose its SQL-writing endpoints externally**.

## Verification and deployment

* Build the Vue frontend (`cd frontend && npm run build`).
* Run unit tests (`.venv/bin/pytest -q tests/permissions/test_permissions_unit.py tests/opportunities/test_opportunity_unit.py tests/email-approval/test_approval_unit.py`).
* Before deploying, apply the migration through the normal database startup
  migration runner, then verify existing roles, a custom permission override,
  revocation mid-session, role changes, industry-booking scope, and recovery
  link revocation against an isolated test database.
* Do not deploy to production or remove standing-superuser recovery access
  as part of the permission editor change.
