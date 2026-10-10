# Request 07 — sale end datetimes and end-of-day hold deadlines (2026-10-10)

> events need to have a sale end datetime. and the reservation can't pass this, so ticket
> held for 1 day, if bought at 12:00 and sale end is 16:00 they have until 16:00, not
> whatever the hold time is. there should be two, one where you can't reserve tickets
> anymore and the other is when union shop sales stop, so people shouldn't be able to
> reserve after the first end date and the confirmation max window is the union sale end
> date. ticker held time also needs to be at the end of the next day with a cut off at
> 23:00 where it's at the end of the next day. therefore is the ticket is held for 1 day,
> tickets bought before the end of the same day so that they can be confirmed at the api
> sync and any ticket held after 23:00 is going to be the end of the next day. and this
> applies to all amount of days held, so 2 days would be the same but +1 day in each
> scenario

**Triage: feature (scope change).** Two new event fields, a new reservation gate, and a
change to how every hold's deadline is computed. That is new behaviour on the live
booking path, outside the approved plan → **proposed as section 17, awaiting approval —
not built this round.** Spec: `.plan/17-sale-windows-hold-deadlines.md`.

## Reading (recorded in DECISIONS.md; correct me if wrong)

1. **Two datetimes per event, both optional** (an unset one keeps today's behaviour, so
   existing events don't change underneath anybody):
   - `reservations_close_at` — after this, nobody can reserve (members, guests, waitlist
     claims). Committee-added tickets are *not* blocked (they are the committee's own
     override).
   - `union_sales_close_at` — when the Union shop stops selling. **No hold's deadline may
     pass it**: booked 12:00 with a 1-day hold and a 16:00 Union close → deadline 16:00.
   - Rule: `reservations_close_at ≤ union_sales_close_at` when both are set.
2. **Hold deadline = end of a London calendar day, not "now + N hours".** For a hold of N
   days: deadline is the end of the day (23:59:59 Europe/London) N−1 days after the
   booking date — so a 1-day hold booked at 12:00 runs to the end of *that same day*, and
   the purchase is picked up by the next morning's 06:05 sync (the matcher confirms any
   still-`held` booking, expired or not, so a purchase before midnight confirms the next
   morning). **Booked at or after 23:00 London → one extra day** (a 1-day hold booked at
   23:15 runs to the end of the next day). A 2-day hold is the same, +1 day in both cases.
   Then capped by `union_sales_close_at`.
3. The `hold_hours` columns stay (expand-only); the admin form shows and edits **days**,
   stored as `days × 24`; existing non-multiple values round **up** to whole days.

Ambiguity flagged: the note's first sentence says "end of the next day" for the normal
case, which would make a 1-day hold booked at 12:00 run to the end of *tomorrow*. The
rest of the note ("bought before the end of the same day so that they can be confirmed at
the api sync", "after 23:00 … end of the next day") reads as end-of-*same*-day with the
23:00 roll-over, so that is the reading the spec takes. If you meant end of the next day
(and +1 more after 23:00), say so when approving — it is a one-line change to the rule.
