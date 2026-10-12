-- Bulk email that waits for a person to say yes.
--
-- Nothing that goes to a crowd is sent by a timer any more. The on-sale
-- announcement used to go the moment `sales_open_at` passed; now the timer only
-- writes the campaign, `awaiting_approval`, and a committee member reads it,
-- edits it, and sends or dismisses it. Mail about one person's own booking or
-- sign-in is untouched — it is the answer to something they just did.
--
-- The draft is an ordinary campaign row, so a send it starts is the same
-- resumable send as a newsletter (migrations 035–037): approval flips the row to
-- `sending` and the existing sender takes it from there.
--
-- Expand-only. The status check is widened, never narrowed: the previous image
-- never writes the new values, and reads them as just another status string.
--
-- The old check was declared inline in 029, so its name is whatever Postgres
-- generated. Found by what it checks rather than by a guessed name: a guess
-- that missed would leave the old check in place beside the new one, and every
-- draft insert would fail on it.
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
        'draft', 'awaiting_approval', 'sending', 'sent', 'failed', 'dismissed'
    ));

-- Who it goes to, as edited. `audiences` (migration 029) already holds the keys
-- a draft was addressed to; these two are the hand edits on top of them —
-- addresses added one by one, and addresses taken off. Kept as edits rather
-- than as a frozen list so the audience is resolved again at the moment of
-- sending: somebody who unsubscribes while a draft waits is not mailed by it.
--
-- Real addresses, in JSONB, which verify.sql's text-column scan cannot see —
-- so scrub.sql empties both by name.
ALTER TABLE public.email_campaigns
    ADD COLUMN IF NOT EXISTS extra_recipients    JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS excluded_recipients JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Who said yes (or no), and when. The status says which it was.
ALTER TABLE public.email_campaigns
    ADD COLUMN IF NOT EXISTS reviewed_by BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

-- The queue's own query. Partial, because it is a handful of rows in a table
-- that only grows.
CREATE INDEX IF NOT EXISTS email_campaigns_awaiting_idx
    ON public.email_campaigns (created_at)
    WHERE status = 'awaiting_approval';

-- On-sale campaigns stored a placeholder where the body goes, because the
-- message was built from the event alone. The body is now the editable opening
-- of that message, and an empty one means "the standard opening line" — so the
-- placeholder must not be read back as copy and mailed to anybody resumed.
UPDATE public.email_campaigns
   SET body = ''
 WHERE kind = 'on_sale'
   AND body = '(automatic on-sale announcement)';
