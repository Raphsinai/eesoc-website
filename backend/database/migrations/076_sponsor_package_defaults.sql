-- The standard sponsor package's allowances, set by the committee.
--
-- Until now "one workshop, three marketing shares" was a constant in
-- api/services/agreement.py, so the number every un-customised sponsor got was
-- whatever the code said. A row here replaces the code's figure for every
-- sponsor without their own allowance; a sponsor's own row in sponsor_terms
-- still wins over both.
--
-- No seed: a clause with no row keeps the code's figure, so applying this
-- changes nothing anybody sees until the committee sets a number.

CREATE TABLE IF NOT EXISTS public.sponsor_package_defaults (
    clause     VARCHAR     PRIMARY KEY,
    allowance  INTEGER     NOT NULL CHECK (allowance >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT      REFERENCES public.users(id) ON DELETE SET NULL
);
