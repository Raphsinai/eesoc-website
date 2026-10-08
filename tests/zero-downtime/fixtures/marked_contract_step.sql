-- Zero-downtime check fixture: the approved escape hatch.
--
-- Not a real migration. A destructive change that carries
-- `-- contract-step: <reason>` is a deliberate, reviewed break of the
-- default expand-only contract (the committee merging two duplicate columns
-- into one, say). The check must accept it and print the reason so a reviewer
-- can see what was deliberately approved.
ALTER TABLE public.email_campaigns DROP COLUMN heading;
-- contract-step: merging duplicates