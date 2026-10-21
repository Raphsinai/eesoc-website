-- Optional wording override for the public What's On badge. This is display
-- copy only, never a change to capacity, sale windows or reservation rules.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS availability_badge VARCHAR NOT NULL DEFAULT 'auto'
    CHECK (availability_badge IN ('auto', 'not_many_left', 'selling_fast'));
