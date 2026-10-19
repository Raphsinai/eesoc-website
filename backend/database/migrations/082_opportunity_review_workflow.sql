-- Opportunity feedback and submitter-to-committee email handoff. Expand-only:
-- this preserves existing approved listings and queued campaigns from 081.
ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS editable_after_rejection BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS announcement_subject VARCHAR(180),
    ADD COLUMN IF NOT EXISTS announcement_body TEXT;
