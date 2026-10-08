-- Committee-added tickets, and a record of every booking confirmation email.
--
-- Expand-only: new columns with defaults, nothing renamed or dropped, so the
-- previous image keeps working against this schema if production is rolled
-- back.

-- Where a booking came from. 'committee' is a ticket a committee member added
-- for somebody — paid in cash, by transfer, or comped — which lands confirmed
-- and never goes through the hold / Union-sale match at all. `added_by` is who.
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS source VARCHAR NOT NULL DEFAULT 'booking';
ALTER TABLE public.reservations
    ADD CONSTRAINT reservations_source_check CHECK (source IN ('booking', 'committee'));
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS added_by BIGINT REFERENCES public.users(id) ON DELETE SET NULL;

-- The "you're confirmed" email, per booking.
--
--   NULL        not sent yet. A confirmed booking with NULL here is what the
--               sender picks up, whichever path confirmed it — the matcher, a
--               committee "Mark bought", a committee-added ticket — so a new
--               way of confirming can never quietly skip the email.
--   sending     claimed by a sender; reclaimed if it sits here too long.
--   sent        delivered to the mail server, at confirmation_sent_at.
--   failed      the mail server refused or was unreachable; retried.
--   skipped     deliberately not sent (see below, or the committee untick).
--   suppressed  the address is not allowed to receive mail here (staging's
--               MAIL_ALLOWLIST), so nothing was attempted.
ALTER TABLE public.reservations ADD COLUMN IF NOT EXISTS confirmation_email VARCHAR;
ALTER TABLE public.reservations
    ADD CONSTRAINT reservations_confirmation_email_check CHECK (
        confirmation_email IS NULL OR confirmation_email IN
            ('sending', 'sent', 'failed', 'skipped', 'suppressed')
    );
ALTER TABLE public.reservations ADD COLUMN IF NOT EXISTS confirmation_sent_at TIMESTAMPTZ;
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS confirmation_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS confirmation_updated_at TIMESTAMPTZ;

-- Every booking confirmed before today was confirmed with no email, and stays
-- that way. Without this, the first sender pass after the deploy would email
-- every confirmed booking in the database at once.
UPDATE public.reservations SET confirmation_email = 'skipped'
 WHERE status = 'confirmed' AND confirmation_email IS NULL;

CREATE INDEX IF NOT EXISTS reservations_confirmation_pending_idx
    ON public.reservations (confirmed_at)
    WHERE status = 'confirmed'
      AND (confirmation_email IS NULL OR confirmation_email IN ('sending', 'failed'));
