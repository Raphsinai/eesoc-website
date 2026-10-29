# Connected societies — membership, committee, announcements and WhatsApp

This integration mirrors each society's Union/SUMS members independently from EESoc. It does **not** infer society membership from an Imperial email address. The authoritative records are the imported current-academic-year SUMS roster, and booking endpoints recheck eligibility server-side. A student may qualify for a ticket by EESoc membership (when the EESoc-only switch is on), **or** by membership of any named eligible society; unrestricted tickets remain unaffected.

## Setup (EESoc committee)

1. Grant individual action permissions in **Admin → People** (`societies.*` and `events.ticket_types.eligibility`); scopes and grants stay revocable action by action. The standing superuser has these rights automatically.
2. Go to **Admin → Societies**, enter a cooperating society's display name, SUMS centre code and API key, and **Sync members**. Keys are encrypted at rest, never returned to the browser, and can be rotated. Only connect societies that have authorised EESoc to process their member information and contact addresses.
3. Grant named external committee members by Imperial **Login**. Select `managing` for people allowed to administer assigned events, then tick individual actions. A committee member is *not* automatically an EESoc administrator.
4. Assign specific existing events to each society. External managers can edit and publish only their assigned events, using the individual permissions provided. They cannot assign events or edit ticket eligibility, which remain EESoc committee operations.
5. In an EESoc event's ticket editor, choose eligible societies. The backend checks the membership feed again when confirming/claiming reservations. A society-linked ticket's selected societies are alternatives, not a requirement to be a member of every one.
6. External committee members sign in with their verified `@ic.ac.uk` or `@imperial.ac.uk` address and open **Society committee** in the site header (`/societies`). Their dashboard shows only their own society and assigned events.

The imported mailing list is built from the Union `Login` field (strictly validated, lowercased, de-duplicated), then `@ic.ac.uk` is appended. The export and email selector exclude global and relevant topic opt-outs, and explicit negative subscription preferences on matching accounts. Importing the list does not subscribe it to unrelated topics. Bulk mail retains ordinary unsubscribe links and the staging mail allowlist.

## Announcement scope

A society committee's announcement tool exposes only the EESoc test list, that same society's current synced membership addresses, and WhatsApp groups assigned to the society. No EESoc `members`, `accounts`, third-party list, or another society's list is accepted, even in crafted requests. Viewing previews, emailing and posting to WhatsApp have separate grants; sending requires a managing committee member. EESoc users need `societies.members.mail` before the main email editor can target a connected society's mailing list.

## WhatsApp

The EESoc account and each society number have separate linked-device sessions and storage. An external manager may link/unlink only their society number, and toggle availability only for groups that number can actually post in. If a society has no linked number, its manager can request that the EESoc number join a group via an invite link. An EESoc committee member with `societies.whatsapp.invites.approve` must explicitly approve the join. Invites are stored encrypted and not returned on status listings.

Under **Admin → WhatsApp → Group access control**, groups start disabled, can be assigned to a society, and may separately be made available to named EESoc committee user IDs. Those EESoc users additionally require `whatsapp.post`. The server rechecks the current group and user grants at dispatch time; having access to the group-management panel is **not** posting permission. Group ownership and disable flags can be changed by an EESoc authorised administrator. External society managers cannot grant EESoc group posting access.

WhatsApp integration uses an unofficial Web protocol. It may be rate limited or banned by WhatsApp. Do not add groups without their administrators' consent; review the group and the account before enabling. Separate accounts are isolated in `/data/session/societies/society-<ID>` in the WhatsApp volume; the main EESoc account continues using `/data/session`.

## Deployment / security

`EXTERNAL_SOCIETY_SECRET` must be an independently generated, stable random secret of at least 32 characters set for the API container. A missing secret causes credential operations to fail closed with HTTP 503. Changing it without re-encrypting stored keys makes those keys unreadable; back it up in the cluster's secret-management process. Staging's `eesoc-staging/eesoc-secrets` includes this key. **Production has not been configured or deployed**, and requires a separately provisioned key on approval.

Membership sync occurs in the existing daily Union sync and supports an explicit per-society Sync now action. One cross-pod lease per society prevents duplicate upstream requests, and the Union bridge globally throttles all requests. Failed fetches leave the last membership snapshot in place; society-eligibility checks do not accept last year's snapshot. The page displays last successful sync and any fetch error. The Union bridge is internal-only.

To verify in staging: connect a disposable/test society with the Union's permission, confirm the expected row count and `Login@ic.ac.uk` addresses, assign a test event, verify a member and nonmember cannot swap tickets through direct API calls, check that one society committee cannot view/edit another's event or mailing list, and confirm WhatsApp group posts are refused until both group and user grants are enabled. Never use real student accounts or groups in tests without authorisation; the automated test stack has a Union stub and mail sink.
