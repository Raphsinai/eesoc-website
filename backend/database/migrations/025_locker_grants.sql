-- Free locker allocations.
--
-- The old Cloudflare system kept a `freeLockers` map of shortcode -> count, so
-- committee members did not have to buy one. Entitlement here was strictly
-- "lockers purchased on the Union shop", which would have made committee pay.
--
-- Keyed on shortcode rather than user id: a grant is made before the person has
-- necessarily signed in, and the shortcode is the stable handle for an Imperial
-- person (the same key sign-in canonicalises to).
CREATE TABLE IF NOT EXISTS public.locker_grants (
    shortcode     VARCHAR     NOT NULL,
    academic_year VARCHAR     NOT NULL,
    free_count    INTEGER     NOT NULL DEFAULT 1 CHECK (free_count > 0),
    note          VARCHAR,
    granted_by    BIGINT      REFERENCES public.users(id) ON DELETE SET NULL,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Per year: a grant should lapse at handover rather than persisting for
    -- somebody who has left the committee.
    PRIMARY KEY (shortcode, academic_year)
);

CREATE INDEX IF NOT EXISTS locker_grants_year_idx
    ON public.locker_grants (academic_year);
