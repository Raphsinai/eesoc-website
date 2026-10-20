# EESoc committee: action permissions and role presets

**Permanent requirement:** every non-self-service administrative action has a stable, individually revocable permission in [`backend/api/utils/roles.py`](../backend/api/utils/roles.py). Every future coding agent must follow [`AGENTS.md`](../AGENTS.md).

## People's permissions editor

In **Admin → People**, select a role to preselect defaults and then customise any action. The editor offers searchable nested categories and subcategories, with **select all, clear all, indeterminate/partial checkboxes**, and every individual action separately selectable. Categories select multiple *real* action grants; they are never stored as a wildcard or as a permission themselves.

The server supplies role presets and the permission catalogue from `GET /admin/admins`; no independent, drifting frontend permission list is maintained. Only `access.grant` can assign access; `access.revoke` removes a user's role; `access.impersonate` is independently controlled. Even a superuser's privileges are checked by named actions; the configured standing superuser is protected from demotion.

### Examples of independent controls

| User-facing operation | Action key |
| --- | --- |
| Create event | `events.create` |
| Edit existing event | `events.edit` |
| Publish/unpublish event | `events.publish` |
| Delete event | `events.delete` |
| Add/edit/remove ticket type | `events.ticket_types.create` / `.edit` / `.delete` |
| Add attendee ticket | `events.bookings.add` |
| Confirm or cancel attendee ticket | `events.bookings.confirm` / `.cancel` |
| Remind an attendee to pay | `events.bookings.remind` |
| Change attendee ticket type | `events.bookings.change_ticket` |
| Resend confirmation | `events.bookings.resend` |
| Download booking CSV | `events.bookings.export` |
| View door list, scan ticket, manual check-in, override invalid ticket, undo | `events.checkin.view` / `.scan` / `.manual` / `.override` / `.undo` |
| Grant, revoke, impersonate user | `access.grant` / `.revoke` / `.impersonate` |
| View, approve, reject opportunity | `opportunities.view` / `.approve` / `.reject` |
| Edit email queue, send test, approve, schedule, unschedule, dismiss | `email.queue.edit` / `.test` / `.send` / `.schedule` / `.unschedule` / `.dismiss` |
| Post to WhatsApp | `whatsapp.post` |

The full catalogue also separately covers sponsors, sponsor marketing and portal operations, resources, shared file uploads, product links, committee page, lockers, allowances and Union sales sync. **No named role implicitly overrides a permission.** Scope and action are independent: a booking cancellation right also needs either `events.scope.all` or `events.scope.industry` on the matching event, verified by the backend; a read permission never implies a write permission.

## Database representation, compatibility, and security

Migration `083_fine_grained_permissions.sql` already added `public.admins.permissions TEXT[] DEFAULT NULL`. No new schema is needed when adding individual permission identifiers.

- `NULL`: use the chosen role's current preset, for previously uncustomised users.
- `[]`: keep the role label but grant zero actions.
- `['events.bookings.view', 'events.bookings.cancel', 'events.scope.industry']`: exact per-person grants; changing the role does not widen a custom set until the operator explicitly reselects its defaults.
- Old `['events', 'bookings', ...]` records written by the earlier version are expanded only **on read** via `LEGACY_GRANTS` in `roles.py`. Old broad IDs can no longer be saved or selected; an operator resaving the user converts the effective grants into current, individually editable keys.

Permission arrays are validated against `PERMISSIONS`, duplicates and unknown actions rejected. `AuthSession` is resolved afresh from the database on each request so revocation takes effect without relogin. HTTP route guards `require_action` and dynamic `session.can` checks are authoritative; client-side visibility is only for clarity. `events.scope.*` constraints are additional and never suffice by themselves. Public and member/sponsor self-service routes retain their ownership checks and are not committee permissions.

For combined email and WhatsApp sends, validate the specific selected audience channels, not merely an `email` or `whatsapp` category. For an event edit form containing ticket types, the backend compares ticket changes and checks ticket-type create/edit/delete permissions separately from `events.edit`; publication requires `events.publish` separately.

**Never expose the database adapter's SQL-writing endpoints publicly.** CSRF is required for state changes; account recovery links must still enforce extra authorization for committee targets. Commit only coherent, tested work; push to staging via `main` and do not promote to production without explicit approval.

## Checks for every future change

`pytest -q tests/permissions/` and `cd frontend && npm run build`. The audit test enumerates protected administrative route decorators and catches unknown/unprotected permission controls. Add denied (403) as well as allowed cases for new actions, including cross-event and cross-sponsor access. Run the full compose/E2E suites in CI and verify the staging release independently; do not assume a successful Git push means ArgoCD has reconciled.
