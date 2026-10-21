# What's On availability badges

The event calendar and its EventCard never display numerical places remaining.
The API's event status is derived centrally in `backend/api/utils/present.py`.
Reservation capacities and individual ticket availability remain enforced exactly
as before. The full event-detail and private admin booking pages may still
show capacity numbers to perform reservations or manage the door.

## Automatic (default)

- No ticket types: **Free · just turn up**; external sign-up: **Sign up**.
- Tickets announced but not open: **On sale soon**, or the scheduled date.
- No ticket on sale in the current window: the next ticket's on-sale date,
  **Sold out**, or **Booking closed**. These states always take precedence.
- Tickets on sale with **1–9** places remaining: **Not many left**.
- Otherwise, paid events display **On sale**; free or entirely members-only
  events retain their informative **Free entry** / **Members only** labels.
- If the event has a total capacity, both the overall remaining seats and
  the sum of the currently available ticket-type seats are considered; the
  smaller limit wins. An uncapped active ticket type means that combined
  per-type availability is unknown unless there is an event-level cap.

## Manual labels

From **Committee admin → Events**, an officer with the independent permission
`events.availability_badge.edit` and appropriate event scope can choose a
**What's On label** beside the event. Choices are **Automatic** (default),
**Not many left**, and **Selling fast**. The selection saves immediately.
Neither override masks a closed, unpublished, sold-out or not-yet-on-sale event.

The setting is a column on `public.events` introduced in migration 085, with a
separate PATCH endpoint. Ordinary event edits preserve the current choice,
including edits made by someone who does not have permission to change this
label. The new permission is a fine-grained action and not bundled into
`events.edit`.
