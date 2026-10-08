-- Doors close: the time after which nobody gets in, even with a ticket.
--
-- Nullable and expand-only: NULL means "no doors-close time", the previous
-- image keeps working against the schema, and nothing existing reads the
-- column. Not personal data, so scrub.sql is not involved.
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS doors_close_at TIMESTAMPTZ;
