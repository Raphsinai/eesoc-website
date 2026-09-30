-- Make login the actual key for committee profiles.
--
-- 019 added a *partial* unique index (WHERE login IS NOT NULL). Postgres will
-- not infer a partial index in `ON CONFLICT (...)` unless the predicate is
-- repeated there, so every upsert failed with "no unique or exclusion
-- constraint matching the ON CONFLICT specification".
--
-- Login is always supplied — it is the path parameter — so it can simply be
-- NOT NULL with a plain unique index. CID becomes optional: a profile may be
-- written before the roster sync has told us the person's CID.

DELETE FROM public.committee_profiles WHERE login IS NULL OR login = '';

DROP INDEX IF EXISTS committee_profiles_login_key;

ALTER TABLE public.committee_profiles ALTER COLUMN login SET NOT NULL;

-- CID was the primary key; it is now just a recorded attribute.
ALTER TABLE public.committee_profiles DROP CONSTRAINT IF EXISTS committee_profiles_pkey;
ALTER TABLE public.committee_profiles ALTER COLUMN cid DROP NOT NULL;

ALTER TABLE public.committee_profiles
    ADD CONSTRAINT committee_profiles_login_key UNIQUE (login);
