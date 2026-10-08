-- Door check-in: one row per booking the committee has let in.
--
-- PRIMARY KEY on reservation_id is what makes check-in idempotent: two
-- committee members scanning the same ticket on two phones in the same second
-- produce one row, and the second is told who got there first. A booking for
-- several people is one ticket and one row — the door screen says "× 3".
--
-- Numbered 060 rather than plan 06's 045: the plan's section is still
-- in progress on its own branch and must drop its copy of this table when it
-- merges (same name, same shape).
CREATE TABLE IF NOT EXISTS public.reservation_checkins (
    reservation_id BIGINT      PRIMARY KEY
                   REFERENCES public.reservations(id) ON DELETE CASCADE,
    event_id       BIGINT      NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
    checked_in_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 'scan' from a QR, 'manual' from the list; 'override' when the committee
    -- let in a booking that was not confirmed (unpaid, say).
    method         VARCHAR     NOT NULL DEFAULT 'scan'
                   CHECK (method IN ('scan', 'manual', 'override')),
    checked_by     BIGINT      REFERENCES public.users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS reservation_checkins_event_idx
    ON public.reservation_checkins (event_id, checked_in_at);
