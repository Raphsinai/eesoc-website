-- Each ticket type can go on sale, and come off sale, at its own time.
--
-- Early bird and general admission are the case this is for: early bird from
-- Monday until Friday 10:00, general from Friday 10:00. The event's own
-- `sales_open_at` still says when the event goes on sale at all — it is what
-- the "tickets are on sale" email and the event page's "On sale soon" read —
-- and a ticket type's times narrow that window, never widen it:
--
--   sales_open_at   — this type cannot be booked before then. NULL = it opens
--                     with the event.
--   sales_close_at  — this type cannot be booked from then on. NULL = it sells
--                     until the event's booking closes.
--
-- Both NULL keeps today's behaviour, so no existing ticket changes underneath
-- anybody. Not personal data, so scrub.sql is not involved.
--
-- Expand-only: two nullable columns and a check every existing row passes.

ALTER TABLE public.ticket_types ADD COLUMN IF NOT EXISTS sales_open_at TIMESTAMPTZ;
ALTER TABLE public.ticket_types ADD COLUMN IF NOT EXISTS sales_close_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ticket_types_sale_window_order') THEN
        ALTER TABLE public.ticket_types
            ADD CONSTRAINT ticket_types_sale_window_order CHECK (
                sales_open_at IS NULL
                OR sales_close_at IS NULL
                OR sales_open_at < sales_close_at
            );
    END IF;
END $$;
