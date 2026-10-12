-- Free tickets are places, not holds.
--
-- A booking on a ticket with nothing to buy (`requires_purchase` false) used to
-- be a hold like any other, lasting until the event: shown as "held", never
-- confirmed, and a waitlist offer of one lapsed after the hold time because
-- nothing could ever confirm it. From this release such a booking is confirmed
-- the moment it is made (database/routes/events.py, `_confirms_outright`).
-- This brings the bookings already taken into line.
--
-- Data only: no column, table or constraint changes, so the previous image
-- keeps working against it mid-rollout (and treats these rows as the
-- confirmed bookings they now are).
--
-- Not converted:
--   * paid holds — they still wait for a Union sale;
--   * expired holds — the event has started; there is nothing left to give;
--   * free members-only holds — whether the person is a member *this* year
--     needs the academic year, which only the api knows. The matcher's
--     membership pass confirms each one at the next sync if the feed shows
--     them (`match_reservations`), and leaves the rest waiting, as before.
--
-- No email burst. A confirmed booking with `confirmation_email IS NULL` is
-- what the confirmation sender picks up, so converting a few hundred holds and
-- leaving it NULL would send a few hundred emails at once from the shared
-- Imperial mailbox — enough to trip Exchange's per-minute limit, which is the
-- same mailbox that sends sign-in codes. Every row touched here, the
-- members-only ones included, is marked 'skipped' instead. Section 16's
-- catch-up (migration 064, `.plan/16-confirmation-ticket-link.md`) re-queues
-- exactly these — confirmed or free-held, never sent a QR, upcoming event —
-- and paces its send; that is the place to email them, with the approval its
-- production promote needs.

UPDATE public.reservations r
   SET status       = CASE WHEN tt.members_only THEN r.status ELSE 'confirmed' END,
       -- They were places from the moment they were booked.
       confirmed_at = CASE WHEN tt.members_only THEN r.confirmed_at ELSE r.created_at END,
       confirmation_email = 'skipped',
       confirmation_updated_at = NOW()
  FROM public.ticket_types tt
 WHERE tt.id = r.ticket_type_id
   AND NOT tt.requires_purchase
   AND r.status = 'held'
   AND r.expires_at > NOW()
   AND r.confirmation_email IS NULL;
