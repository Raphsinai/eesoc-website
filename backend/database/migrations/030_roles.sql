-- Roles, impersonation, and the two things they need to be about.
--
-- Until now "committee access" was one bit: a row in `admins`. That is too
-- coarse for a committee of twenty, where the events officers should not be
-- able to delete a sponsor, the sponsorship officers should not be able to
-- hand out admin access, and a member who only wants to fix the typo in their
-- own committee blurb should not need the keys to the whole site.
--
-- So the row gains a role. One role per account, deliberately: the four are
-- not independent axes, they are four jobs, and a set would only invite the
-- question of what "events + industry but not admin" is supposed to mean.
--
--   superuser  everything, plus granting roles and impersonating
--   admin      everything except user management
--   events     events and their bookings
--   industry   sponsors, and the events marked as industry events
--
-- Anyone on the committee roster can edit their own card whether or not they
-- hold any of these — that is not a role, it is ownership of a row.

ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS role VARCHAR NOT NULL DEFAULT 'admin';

-- Written as a named constraint added separately so re-running the migration on
-- a database that already has it is a no-op rather than an error.
ALTER TABLE public.admins DROP CONSTRAINT IF EXISTS admins_role_check;
ALTER TABLE public.admins
    ADD CONSTRAINT admins_role_check
    CHECK (role IN ('superuser', 'admin', 'events', 'industry'));

-- Every existing grant was full access, so they stay full access.
UPDATE public.admins SET role = 'admin' WHERE role IS NULL OR role = '';

-- The standing superuser. The api service pins this address to superuser at
-- request time as well, so the role cannot be lost by an errant DELETE, but
-- seeding it here means the row exists and shows up in the people list.
INSERT INTO public.admins (user_id, role, note)
SELECT id, 'superuser', 'standing superuser'
  FROM public.users
 WHERE lower(email) = 'ras124@ic.ac.uk'
ON CONFLICT (user_id) DO UPDATE SET role = 'superuser';


-- Impersonation.
--
-- A superuser debugging "it does not work for me" needs to see what that person
-- sees. The honest way to do that is a real session for the real account, with
-- a record of who actually opened it — so every audit trail says the superuser
-- was there, and the banner on the page says it too. NULL is the normal case:
-- somebody signed in as themselves.
ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS impersonator_id BIGINT
        REFERENCES public.users(id) ON DELETE CASCADE;


-- Which account owns a committee card.
--
-- Normally the match is by login: the roster row says `ab1234` and that is the
-- shortcode on the account. It fails for the committee member whose account is
-- under a personal address, or whose ICU record carries a login the account
-- never saw — and then they cannot edit their own card. This column is the
-- manual override for exactly that case: an admin links the address, and the
-- person who signs in with it owns the card.
ALTER TABLE public.committee_profiles ADD COLUMN IF NOT EXISTS user_email VARCHAR;

CREATE UNIQUE INDEX IF NOT EXISTS committee_profiles_user_email_key
    ON public.committee_profiles (lower(user_email)) WHERE user_email IS NOT NULL;


-- Industry events.
--
-- The industry role may edit events, but only the ones that are its own work:
-- the careers fair, a sponsor's talk, a site visit. `category` cannot carry
-- that — it is free text that the events form lets anybody retype — so the
-- ownership is an explicit flag. An event created by somebody holding only the
-- industry role is marked automatically; anyone with full access can set it by
-- hand to hand an event over.
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS industry BOOLEAN NOT NULL DEFAULT FALSE;

-- An event requested by a sponsor is an industry event by construction.
UPDATE public.events SET industry = TRUE WHERE requested_by_sponsor_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS events_industry_idx ON public.events (industry) WHERE industry;
