-- Existing waitlists have always been allowed while bookings are open. Keep
-- that behaviour for existing events, while allowing committee to close new
-- waitlist sign-ups per event without deleting anyone already queued.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS waitlist_enabled BOOLEAN NOT NULL DEFAULT TRUE;
