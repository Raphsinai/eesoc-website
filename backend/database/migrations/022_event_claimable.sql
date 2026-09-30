-- Sponsorship claimability is now explicit, and off by default.
--
-- Previously *every* published future event with no existing claim was offered
-- to sponsors, which is wrong in both directions: most events are not for sale,
-- and an event a sponsor themselves requested would appear in their own
-- "claimable" list.
--
-- Default false so nothing becomes claimable by accident — the committee opts an
-- event in.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS sponsorship_claimable BOOLEAN NOT NULL DEFAULT false;

-- Records which sponsor asked for an event, when one was created from a request.
-- Such events are never claimable on creation: the arrangement is already being
-- negotiated by email, and offering it back to the queue would let a different
-- sponsor take it.
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS requested_by_sponsor_id BIGINT
        REFERENCES public.sponsors(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS events_claimable_idx
    ON public.events (sponsorship_claimable, starts_at)
    WHERE published AND sponsorship_claimable;
