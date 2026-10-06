-- Tie a guest's bookings to their browser.
--
-- A guest has no session, so after booking, a refresh showed them the Reserve
-- button again with no sign of what they had just done — and they booked
-- again. The API now gives the browser an anonymous visitor cookie when
-- Reserve is pressed and stores its SHA-256 here, so the event page can show a
-- guest their own booking and the one-per-person limit can count it.
--
-- Numbered 032 although production went 028 -> 032: 029-031 belong to work
-- still on staging. Migrations are tracked by number, so those apply after
-- this one when they are promoted, and nothing here touches what they do.
ALTER TABLE public.reservations ADD COLUMN IF NOT EXISTS visitor_hash CHAR(64);

CREATE INDEX IF NOT EXISTS reservations_visitor_idx
    ON public.reservations (event_id, visitor_hash)
    WHERE visitor_hash IS NOT NULL;
