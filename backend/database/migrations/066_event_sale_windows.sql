-- Sale windows: when booking stops, and when the Union shop stops selling.
--
-- Two optional times per event (section 17):
--
--   reservations_close_at  — after this nobody can reserve: members, guests,
--                            guest drafts, waitlist offers. Committee-added
--                            tickets are not gated; they are the committee's
--                            own override.
--   union_sales_close_at   — when the Union shop stops selling this event. No
--                            hold's pay-by deadline may pass it: booked at
--                            12:00 with a 16:00 close, you have until 16:00.
--
-- Both NULL keeps today's behaviour, so no existing event changes underneath
-- anybody. Booking cannot close after selling does, which the check holds in
-- SQL as well as in the form.
--
-- Expand-only: two nullable columns and a check every existing row passes.

ALTER TABLE public.events ADD COLUMN IF NOT EXISTS reservations_close_at TIMESTAMPTZ;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS union_sales_close_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_sale_windows_order') THEN
        ALTER TABLE public.events
            ADD CONSTRAINT events_sale_windows_order CHECK (
                reservations_close_at IS NULL
                OR union_sales_close_at IS NULL
                OR reservations_close_at <= union_sales_close_at
            );
    END IF;
END $$;
