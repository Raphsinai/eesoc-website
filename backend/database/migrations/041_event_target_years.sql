-- Which year groups an event is aimed at, as a comma-separated string of year
-- numbers ("1,2"). NULL or '' means aimed at everyone. Stored as a flat string
-- rather than a join table because it is only ever read back as one whole
-- list; the email queue (section 09) will read it to aim an event's
-- announcement at the right department year-group lists.
--
-- It is a list of year numbers, not personal data, so nothing here needs
-- adding to deploy/snapshot/scrub.sql.
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS target_years TEXT;
