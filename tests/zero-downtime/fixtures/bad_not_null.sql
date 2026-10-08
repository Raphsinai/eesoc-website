-- Zero-downtime check fixture: NOT NULL without a DEFAULT on a new column.
--
-- Not a real migration. A NOT NULL ADD COLUMN with no DEFAULT fails outright
-- on any table that already has rows, so it cannot be applied as a single
-- expand step; it needs a DEFAULT (later removed once backfilled) or a
-- nullable column backfilled in a second step.
ALTER TABLE public.email_campaigns ADD COLUMN reply_to VARCHAR NOT NULL;