-- Committee-submitted order numbers must be reconciled to an actual Union sale.
-- A missing sale may appear only on the next morning's daily refresh: keep the
-- reservation held, and reject only the unverified order number after a
-- successful sales refresh at/after 06:05 UTC the next calendar day.
CREATE TABLE IF NOT EXISTS public.reservation_order_verification (
    reservation_id BIGINT PRIMARY KEY REFERENCES public.reservations(id) ON DELETE CASCADE,
    order_number VARCHAR(128) NOT NULL,
    submitted_by BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    check_after TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','matched','rejected')),
    checked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS order_verification_pending_idx
    ON public.reservation_order_verification (check_after)
    WHERE status='pending';

-- Preserve standing superuser override access in the editable role catalogue.
UPDATE public.committee_roles
   SET capabilities = array_append(capabilities, 'events.bookings.confirm_override')
 WHERE id = 'superuser'
   AND NOT ('events.bookings.confirm_override' = ANY(capabilities));
