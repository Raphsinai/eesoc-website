-- When ticket sales open.
--
-- NULL means "not announced yet" — the site shows "On sale soon" and refuses
-- to take a booking. That is the state a newly created event starts in, so a
-- draft cannot accidentally sell tickets before the committee has decided.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS sales_open_at TIMESTAMPTZ;

-- Existing events were on sale from the moment they were published, and NULL
-- now means the opposite. Backfill them to their creation time so nothing that
-- is currently bookable closes the instant this ships.
UPDATE public.events
   SET sales_open_at = created_at
 WHERE sales_open_at IS NULL;
