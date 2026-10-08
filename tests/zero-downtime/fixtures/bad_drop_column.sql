-- Zero-downtime check fixture: dropping a column.
--
-- Not a real migration. Once a deployed api has written a column, dropping it
-- is a two-part contract (stop writing, wait for a full deploy, then drop in a
-- later release). This fixture lets the check prove it flags the naive
-- one-step version.
ALTER TABLE public.email_campaigns DROP COLUMN heading;