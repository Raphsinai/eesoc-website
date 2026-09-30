-- Remove the CV book; add a per-sponsor CV contact point instead.
--
-- EESoc is not permitted to collect and pass on members' CVs, so the opt-in flag
-- and the "CV book" idea go entirely. Keeping a column recording consent for
-- something we are not allowed to do would be storing personal data for no
-- lawful purpose, so it is dropped rather than left unused.
--
-- Replaced by the sponsor publishing where to send a CV directly. The member
-- sends it themselves; EESoc never handles it and never sees who applied.
ALTER TABLE public.users DROP COLUMN IF EXISTS cv_opt_in;

ALTER TABLE public.sponsors ADD COLUMN IF NOT EXISTS cv_contact_email VARCHAR;
ALTER TABLE public.sponsors ADD COLUMN IF NOT EXISTS cv_contact_name VARCHAR;
-- Free text: "put EESoc in the subject line", "PDF only", and so on.
ALTER TABLE public.sponsors
    ADD COLUMN IF NOT EXISTS cv_contact_note VARCHAR;
