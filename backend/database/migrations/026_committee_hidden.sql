-- Hide a person from the committee page without waiting for ICU's roster.
--
-- People leave committee mid-year, and ICU's feed keeps listing them until the
-- Union's own records are updated. Deleting the roster row does not work: a
-- re-sync replaces every row from the feed and they reappear, silently, months
-- later. The flag lives on the editorial overlay, which is keyed on login and
-- is exactly the half a sync does not touch.
ALTER TABLE public.committee_profiles
    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;
