-- What a campaign is, and which event it was about.
--
-- A send interrupted by a pod restart can be resumed by a *different* process,
-- which never saw the closure that built the messages. So the campaign row has
-- to carry enough to rebuild one from the stored data alone: `kind` says which
-- builder (newsletter, event announcement, on-sale), `event_id` which event
-- the latter two are about. Without this the only resumable send is the one a
-- restarted process could reconstruct by luck.
--
-- `event_id` is deliberately bare, with no FK: events are reference data that
-- a committee edit can delete, and a campaign is an audit record that must
-- outlive its subject. The resume path checks the event still exists and
-- marks the campaign failed with a clear message if it does not, rather than
-- a FK refusing the write here.
--
-- ADD COLUMN with a DEFAULT is expand-safe: the old code ignores both columns
-- and keeps running while the deploy rolls.
ALTER TABLE public.email_campaigns
    ADD COLUMN IF NOT EXISTS kind VARCHAR NOT NULL DEFAULT 'newsletter',
    ADD COLUMN IF NOT EXISTS event_id BIGINT;
