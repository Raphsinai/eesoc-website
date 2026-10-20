# EESoc website — rules for every coding agent

**Read [`CLAUDE.md`](CLAUDE.md) for environment, safety and deployment constraints.** This file is mandatory for Codex, Claude, Copilot, automated code agents, contributors using agents, and generated code. Neither a role name nor the visibility of a button provides authorization.

## Mandatory action-level authorization standard

1. **One stable permission per independently actionable operation.** Create, view (when nonpublic), edit, publish, delete, download/export, approve, decline, confirm, cancel, resend, manually override and undo are *different* capabilities. Each admin button, form submit, dropdown action, toolbar action, and API mutation needs its own grant. Never introduce an omnibus permission such as `events`, `site`, `bookings`, `admin` or a category/wildcard grant. Splitting a permission further is preferred to bundling unrelated actions.
2. **The canonical catalogue is [`backend/api/utils/roles.py`](backend/api/utils/roles.py)** (`GROUPS` / `PERMISSIONS`). Define each new `namespace.object.verb` identifier with a human-readable label and precise `group` and `subgroup`. Do not reuse an existing key for a semantically different action. Identifiers are persisted by user ID; rename only with an explicit backward-compatible migration plan.
3. **Protect the API first.** Add `Depends(require_action('namespace.object.verb'))` to every private administrative route (including private `GET`), from `backend/api/utils/deps.py`. Different actions sharing a route or a form must be rechecked *inside the handler*, using `session.can('...')`, based on the actual submitted action or changed fields. Mixed-channel operations may use `require_any_action` at the route and MUST validate *each selected channel* in the handler. Never rely on frontend-only gating. Preserve CSRF for writes.
4. **Protect each frontend action.** Render/enable the corresponding button, form action or control only with `can('exact.permission')` from `frontend/src/stores/session.js`. Do not gate a write merely by a broad role, the tab's visibility, a generic `isAdmin`, or another right such as event editing. For combined forms, guard every subaction and do not send unauthorised field changes. Gating is a UX affordance only; server checks remain essential.
5. **Read is not write; scope is not action.** Event access needs both an action (e.g. `events.bookings.cancel`) and an independently selected scope (`events.scope.all` or `events.scope.industry`); verify the target resource before touching it. Publishing, ticket-type CRUD, adding attendee tickets, confirming, cancelling, QR scanning, manual check-in, overriding invalid tickets and undoing a check-in must all be separately revocable. Do not leak unpublished events, customer lists or sponsor private data via a supposedly harmless listing or dashboard.
6. **Role = preset, not an authorization decision.** The `superuser`, `admin`, `events`, `industry` labels preselect default checkboxes only. Effective grants are per-person, resolved freshly from the database. A NULL override follows the role preset; `[]` means no grants; an explicit list means precisely that selection. Existing **legacy** group grants are expanded on read using `LEGACY_GRANTS`; never issue/store new group grants, and maintain migration compatibility for stored old grants. Standing superuser recovery is immutable.
7. **Categories are only selection shortcuts.** The People editor (`frontend/src/components/PermissionSelector.vue`) supports select-all, group/subgroup tri-state checkboxes, search and individual action checkboxes. Selecting a category adds all of its **individual keys** to the per-user array; no category or wildcard is persisted. Every action stays independently untickable. Keep the catalogue, backend guards and UI synchronized.
8. **Test permission boundaries on every feature.** Include positive and denied cases (403), category/individual independence, scope isolation, privilege escalation, and check that requests with no grant cannot mutate state. Add a guard-audit test so unprotected admin endpoints or unknown permission IDs fail CI. Test both frontend build and Python tests before staging; add focused unit/e2e coverage for each new button.
9. **Self-service is the exception, not a gap.** Public routes and `/me/*` own-account routes should use authentication, ownership and CSRF rules, not committee grants. Explain each exception in audit tests. Do not interfere with member or sponsor self-service.
10. **No production deploy without explicit per-change approval.** Per [`CLAUDE.md`](CLAUDE.md), completed cohesive changes may merge to `main` and deploy to staging. Never modify production manifests or run the production promote workflow merely because staging tests pass.

### Example: events

| UI operation | Permission | Separate additional scope |
| --- | --- | --- |
| View private event calendar | `events.view` | `events.scope.all` / `events.scope.industry` |
| Create event | `events.create` | all / industry |
| Change event details | `events.edit` | all / industry |
| Publish/unpublish | `events.publish` | all / industry |
| Add/edit/remove ticket types | `events.ticket_types.create` / `.edit` / `.delete` | all / industry |
| Add attendee ticket | `events.bookings.add` | all / industry |
| Confirm booking | `events.bookings.confirm` | all / industry |
| Cancel booking | `events.bookings.cancel` | all / industry |
| View door list | `events.checkin.view` | all / industry |
| Scan ticket QR | `events.checkin.scan` | all / industry |
| Manually check in | `events.checkin.manual` | all / industry |
| Override invalid or unpaid ticket | `events.checkin.override` | all / industry |
| Undo check-in | `events.checkin.undo` | all / industry |

See [`docs/COMMITTEE_PERMISSIONS.md`](docs/COMMITTEE_PERMISSIONS.md) for storage and migration details. Run `pytest -q tests/permissions/` and `cd frontend && npm run build` as minimum pre-merge checks.
