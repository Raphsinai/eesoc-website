-- Event times are wall-clock times, stored exactly as the committee typed them.
--
-- They were TIMESTAMPTZ, and the admin form sent `new Date(x).toISOString()`,
-- so a time typed during BST was shifted by an hour on the way in and shown
-- shifted on the way out. Every event is in London; a timezone is information
-- nobody needs and the conversion was only ever a source of off-by-an-hour.
--
-- The existing values were entered from London browsers, so converting them
-- to Europe/London wall time recovers what was originally typed.
ALTER TABLE public.events
    ALTER COLUMN starts_at     TYPE TIMESTAMP USING starts_at     AT TIME ZONE 'Europe/London',
    ALTER COLUMN ends_at       TYPE TIMESTAMP USING ends_at       AT TIME ZONE 'Europe/London',
    ALTER COLUMN sales_open_at TYPE TIMESTAMP USING sales_open_at AT TIME ZONE 'Europe/London';

-- Some events — usually sponsored ones — take sign-ups through their own form
-- rather than our tickets. When set, the event page links there instead of
-- offering a booking.
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS signup_url TEXT;
