-- Sponsorship value, for internal use only.
--
-- Never exposed by any public endpoint: it appears solely on the committee
-- dashboard. Stored in pence as an integer, because floating-point money
-- accumulates rounding errors and this feeds a total.
ALTER TABLE public.sponsors
    ADD COLUMN IF NOT EXISTS amount_pence BIGINT NOT NULL DEFAULT 0
        CHECK (amount_pence >= 0);

-- Free-text note about the deal — what the money covers, when it renews.
-- Also committee-only.
ALTER TABLE public.sponsors ADD COLUMN IF NOT EXISTS internal_note TEXT NOT NULL DEFAULT '';

ALTER TABLE public.sponsors
    ADD COLUMN IF NOT EXISTS agreement_year VARCHAR;

-- `sponsor_users` is already a join table, so several people can hold access to
-- one sponsor. This index just makes "who can log in for this sponsor" cheap.
CREATE INDEX IF NOT EXISTS sponsor_users_sponsor_idx
    ON public.sponsor_users (sponsor_id);
