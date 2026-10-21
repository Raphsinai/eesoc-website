-- Restore submitter-withdrawn listings as reusable private drafts.
-- contract-step: expand the opportunity status check to allow explicit drafts.
ALTER TABLE public.opportunities DROP CONSTRAINT IF EXISTS opportunities_status_check;
ALTER TABLE public.opportunities ADD CONSTRAINT opportunities_status_check
    CHECK (status IN ('draft', 'pending', 'approved', 'rejected', 'withdrawn'));

-- An immutable record of each separate period for which a listing was approved.
-- An end time and end reason are set once on takedown, not by a draft edit.
CREATE TABLE IF NOT EXISTS public.opportunity_publication_periods (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
    published_at TIMESTAMPTZ NOT NULL,
    unpublished_at TIMESTAMPTZ,
    end_reason VARCHAR(32),
    posted_until DATE,
    CHECK (unpublished_at IS NULL OR unpublished_at >= published_at)
);
CREATE INDEX IF NOT EXISTS opportunity_periods_by_owner_idx
    ON public.opportunity_publication_periods (opportunity_id, published_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS opportunity_one_open_period_idx
    ON public.opportunity_publication_periods (opportunity_id) WHERE unpublished_at IS NULL;

-- Backfill the one publication period known to the pre-ledger schema.
-- Earlier cycles, if any, were never separately recorded and cannot be inferred.
INSERT INTO public.opportunity_publication_periods
    (opportunity_id, published_at, unpublished_at, end_reason, posted_until)
SELECT id, approved_at,
       CASE WHEN status = 'approved' THEN NULL
            WHEN status = 'withdrawn' THEN COALESCE(withdrawn_at, updated_at, NOW())
            ELSE COALESCE(reviewed_at, updated_at, NOW()) END,
       CASE WHEN status = 'approved' THEN NULL
            WHEN status = 'withdrawn' THEN 'submitter'
            ELSE 'committee' END,
       posted_until
FROM public.opportunities
WHERE approved_at IS NOT NULL
  AND status IN ('approved', 'withdrawn', 'rejected')
ON CONFLICT DO NOTHING;

-- Preserve old withdraw timestamps in the new history; clear the current
-- approval/email state so these rows behave like new unpublished drafts.
UPDATE public.opportunities
   SET status='draft', approved_at=NULL, email_campaign_id=NULL,
       announcement_subject=NULL, announcement_body=NULL,
       review_notes=NULL, pending_revision=NULL, pending_revision_at=NULL,
       editable_after_rejection=FALSE
 WHERE status='withdrawn';

-- An opportunity may announce a newly approved cycle, while old sent/dismissed
-- campaigns retain their original opportunity_id for audit history.
-- contract-step: replace a lifetime-one-campaign index with a live-only index.
DROP INDEX IF EXISTS public.opportunity_one_campaign_idx;
CREATE UNIQUE INDEX IF NOT EXISTS opportunity_one_active_campaign_idx
    ON public.email_campaigns (opportunity_id)
    WHERE kind = 'opportunity_announcement'
      AND opportunity_id IS NOT NULL
      AND status IN ('awaiting_approval', 'scheduled');
