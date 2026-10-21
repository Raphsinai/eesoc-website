-- A submitter may immediately withdraw an approved listing without committee review.
-- Preserve the original approval and campaign records for moderation/audit history.
ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;

ALTER TABLE public.opportunities
    DROP CONSTRAINT IF EXISTS opportunities_status_check;

ALTER TABLE public.opportunities
    ADD CONSTRAINT opportunities_status_check CHECK (
        status IN ('pending', 'approved', 'rejected', 'withdrawn')
    );
