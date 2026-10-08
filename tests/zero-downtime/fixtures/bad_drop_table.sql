-- Zero-downtime check fixture: a migration that drops a table.
--
-- This is not a real migration and is never applied to a database. It exists
-- so scripts/check-migrations.py has a realistic-looking DROP TABLE to flag in
-- its own test suite: a table one release stops writing to must be dropped in
-- a later release, never in the same step that does the stop.
DROP TABLE public.event_sale_alerts;