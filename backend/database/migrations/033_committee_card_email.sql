-- Let a named account edit a committee card.
--
-- A committee member can edit their own card (intro and photo) with no admin
-- role. Their card is found by their login/shortcode or the address ICU
-- holds; this column is the override for the person whose sign-in address
-- matches neither, set by an admin.
--
-- The same column is also created by 030_roles.sql, which is still on staging.
-- Both are IF NOT EXISTS, so whichever runs second is a no-op.
ALTER TABLE public.committee_profiles ADD COLUMN IF NOT EXISTS user_email VARCHAR;

CREATE UNIQUE INDEX IF NOT EXISTS committee_profiles_user_email_key
    ON public.committee_profiles (lower(user_email)) WHERE user_email IS NOT NULL;
