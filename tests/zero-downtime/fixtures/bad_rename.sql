-- Zero-downtime check fixture: renames, table and column.
--
-- Not a real migration. `ALTER ... RENAME` is a break in place rather than an
-- expand/contract step: readers and writers of the old name stop the moment it
-- applies, so rolling out a renamed table or column needs a compatibility
-- window the same way a drop does.
ALTER TABLE public.email_campaigns RENAME TO newsletter_runs;

ALTER TABLE public.newsletter_runs RENAME COLUMN subject TO title;