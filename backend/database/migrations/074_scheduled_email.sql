-- Scheduled email: approved ahead of time, sent at its moment.
--
-- 073 made automated bulk mail wait for approval, but wrote the on-sale draft
-- only once sales had opened — so "tickets are on sale" then waited on whoever
-- approved it. Now the draft is written as soon as an event has a sale time,
-- the committee approves it whenever suits, and it goes out at that time.
--
--   awaiting_approval → scheduled → sending → sent
--
-- `scheduled` is approved and waiting for `send_at`. Expand-only: the status
-- check is widened (the previous image never writes the new value), and both
-- new columns are nullable.

DO $$
DECLARE
    old_check record;
BEGIN
    FOR old_check IN
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'public.email_campaigns'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format('ALTER TABLE public.email_campaigns DROP CONSTRAINT %I',
                       old_check.conname);
    END LOOP;
END $$;
ALTER TABLE public.email_campaigns
    ADD CONSTRAINT email_campaigns_status_check CHECK (status IN (
        'draft', 'awaiting_approval', 'scheduled', 'sending', 'sent', 'failed',
        'dismissed'
    ));

-- When it should go. For an on-sale email this follows the event's
-- `sales_open_at` — move the sale and the email moves with it. NULL means
-- "as soon as it is approved".
ALTER TABLE public.email_campaigns ADD COLUMN IF NOT EXISTS send_at TIMESTAMPTZ;

-- When a person last changed the draft's words or recipients. A draft nobody
-- has touched is rewritten when its event changes (a new title, a new
-- category); one somebody has edited is theirs, and is left alone.
ALTER TABLE public.email_campaigns ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;

-- One live on-sale email per event, in SQL rather than in the hope that two
-- saves of the same event never race. A draft made under 073 that somehow has
-- a twin keeps the newest; the older is dismissed, not deleted.
UPDATE public.email_campaigns c
   SET status = 'dismissed', error = 'Duplicate on-sale draft, dismissed by migration 074.'
 WHERE c.kind = 'on_sale'
   AND c.status = 'awaiting_approval'
   AND EXISTS (
       SELECT 1 FROM public.email_campaigns newer
        WHERE newer.kind = 'on_sale'
          AND newer.status = 'awaiting_approval'
          AND newer.event_id = c.event_id
          AND newer.id > c.id
   );

CREATE UNIQUE INDEX IF NOT EXISTS email_campaigns_one_live_on_sale
    ON public.email_campaigns (event_id)
    WHERE kind = 'on_sale' AND status IN ('awaiting_approval', 'scheduled');

-- The sender's query: what is due next.
CREATE INDEX IF NOT EXISTS email_campaigns_scheduled_idx
    ON public.email_campaigns (send_at)
    WHERE status = 'scheduled';
