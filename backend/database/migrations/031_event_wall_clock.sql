-- Some events — usually sponsored ones — take sign-ups through their own form
-- rather than our tickets. When set, the event page links there instead of
-- offering a booking.
--
-- (This migration once also turned event times into London wall-clock
-- TIMESTAMPs. That was dropped before it reached production: times stay
-- TIMESTAMPTZ in UTC and are converted to London only for display, which is
-- what production does. The filename is kept so the number stays stable.)
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS signup_url TEXT;
