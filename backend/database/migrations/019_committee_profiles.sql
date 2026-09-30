-- Committee profiles keyed on login rather than CID.
--
-- The editorial half of a committee entry (intro, photo, display role) belongs
-- to the person, and must survive a re-sync that rewrites everything ICU owns.
-- Login is the stable handle for that: it is what identifies the same human
-- across years, and it is what the rest of the system already canonicalises on
-- (shortcode@ic.ac.uk).

ALTER TABLE public.committee_profiles ADD COLUMN IF NOT EXISTS login VARCHAR;

-- An optional override for ICU's post name. Their titles can be unwieldy
-- ("Diversity and Inclusion Officer") and the committee may want something
-- shorter on the card, without that edit being reverted by the next sync.
ALTER TABLE public.committee_profiles ADD COLUMN IF NOT EXISTS display_role VARCHAR;

-- Backfill login from the roster for any profile written before this change.
UPDATE public.committee_profiles p
   SET login = c.login
  FROM public.committee c
 WHERE c.cid = p.cid AND p.login IS NULL AND c.login IS NOT NULL;

-- Drop profiles we cannot key (no login, and no roster row to find one from):
-- they can no longer be matched to a person, so keeping them would only attach
-- somebody else's intro to a future member with the same CID.
DELETE FROM public.committee_profiles WHERE login IS NULL AND cid IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS committee_profiles_login_key
    ON public.committee_profiles (lower(login)) WHERE login IS NOT NULL;
