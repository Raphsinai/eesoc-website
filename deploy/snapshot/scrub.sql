-- Anonymise a production dump before it leaves the production namespace.
--
-- This runs inside the snapshot job's own throwaway Postgres, never against
-- the live database. Order matters: delete first, then rewrite what is left.
--
-- The rule this file implements: staging gets production's CONTENT (events,
-- sponsors, resources, committee, lockers, the shape and volume of bookings)
-- and none of production's PEOPLE. That is what makes staging look like the
-- real site without putting a second copy of Imperial students' personal data
-- on a second internet-facing host.
--
-- Anything added here must keep referential integrity: ids are never changed,
-- only the human-readable columns hanging off them, and every replacement is
-- derived deterministically from the row's own id so the same row scrubs to the
-- same value every night and joins across tables still line up.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Credentials. Deleted, not rewritten.
-- ---------------------------------------------------------------------------

-- Session tokens are hashed, but the hash is the credential: the same row in
-- production authenticates the same person. Copying the table would mean a
-- leak of the staging database is a leak of live sessions.
TRUNCATE TABLE public.sessions;

-- Sign-in codes, their hashes, and the IP that asked for them.
TRUNCATE TABLE public.login_codes;

-- The snapshot is of PRODUCTION's schema, which lags staging's by whatever is
-- waiting to be promoted. So anything scrubbed here that a newer migration
-- introduced is guarded by whether it exists yet — a scrub that fails on a
-- missing table publishes nothing, and staging stops refreshing.

-- Keyed by raw email address, and of no use in staging — an opt-out only
-- matters to the mail that is actually being sent. Sale alerts likewise: the
-- address is personal and the token is an unsubscribe credential. Test alerts
-- are made on staging, not copied to it.
DO $$
BEGIN
    IF to_regclass('public.email_optouts') IS NOT NULL THEN
        TRUNCATE TABLE public.email_optouts;
    END IF;
    IF to_regclass('public.event_sale_alerts') IS NOT NULL THEN
        TRUNCATE TABLE public.event_sale_alerts;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Identities.
-- ---------------------------------------------------------------------------
-- `.invalid` is reserved by RFC 2606 and guaranteed never to resolve. So even
-- if staging is given a working SMTP password and something triggers a
-- waitlist promotion against copied data, the mail cannot reach a real person
-- — it fails at DNS. That is the backstop behind the policy of leaving
-- SMTP_PASS out of the staging secret, not a replacement for it.

UPDATE public.users SET
    email      = 'user' || id || '@staging.invalid',
    first_name = 'Test',
    last_name  = 'User ' || id,
    -- The college shortcode identifies the student as surely as their name.
    shortcode  = CASE WHEN shortcode IS NULL THEN NULL ELSE 'usr' || id END;

UPDATE public.members SET
    first_name = 'Member',
    last_name  = 'No ' || id,
    cid        = '9' || lpad(id::text, 7, '0'),
    email      = 'member' || id || '@staging.invalid',
    login      = 'mem' || id;

-- The Union's sales feed: real purchases by real named students. The rows are
-- kept because reservation matching is one of the things worth testing, and it
-- keys off order_number, which is not personal.
UPDATE public.union_sales SET
    customer_cid        = '9' || lpad(id::text, 7, '0'),
    customer_first_name = 'Customer',
    customer_last_name  = 'No ' || id,
    customer_email      = 'customer' || id || '@staging.invalid',
    customer_login      = 'cust' || id;

-- ---------------------------------------------------------------------------
-- 3. Committee: names stay, contact details go.
-- ---------------------------------------------------------------------------
-- Committee names and post titles are already published on eesoc.com, so
-- keeping them is not a second disclosure — and losing them would make the
-- committee page, one of the most visible on the site, stop looking like the
-- real thing. Their college logins, CIDs, emails and phone numbers are not
-- published, and go.
--
-- cid and login are rewritten with the SAME deterministic expression in both
-- tables, because committee_profiles joins to committee_members on them. A
-- random value per table would silently empty the committee page.

UPDATE public.committee_members SET
    cid     = CASE WHEN cid IS NULL THEN NULL
                   ELSE '9' || lpad((abs(hashtext(cid)) % 10000000)::text, 7, '0') END,
    email   = CASE WHEN email IS NULL THEN NULL
                   ELSE 'committee' || abs(hashtext(email)) || '@staging.invalid' END,
    login   = CASE WHEN login IS NULL THEN NULL
                   ELSE 'cmte' || abs(hashtext(login)) END,
    phoneno = NULL;

-- The Union-cache copy of the roster (migration 009), with the same columns
-- under slightly different names. Same expressions, so it still joins.
UPDATE public.committee SET
    cid      = '9' || lpad((abs(hashtext(cid)) % 10000000)::text, 7, '0'),
    email    = CASE WHEN email IS NULL THEN NULL
                    ELSE 'committee' || abs(hashtext(email)) || '@staging.invalid' END,
    login    = CASE WHEN login IS NULL THEN NULL
                    ELSE 'cmte' || abs(hashtext(login)) END,
    phone_no = NULL;

UPDATE public.committee_profiles SET
    cid        = CASE WHEN cid IS NULL THEN NULL
                      ELSE '9' || lpad((abs(hashtext(cid)) % 10000000)::text, 7, '0') END,
    login      = CASE WHEN login IS NULL THEN NULL
                      ELSE 'cmte' || abs(hashtext(login)) END;

-- user_email arrives with migration 030; guarded for the reason above.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'committee_profiles'
                  AND column_name  = 'user_email') THEN
        UPDATE public.committee_profiles SET
            user_email = 'committee' || abs(hashtext(user_email)) || '@staging.invalid'
          WHERE user_email IS NOT NULL;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 4. Third parties and free text.
-- ---------------------------------------------------------------------------
-- Sponsor companies and their adverts are public. The named person who
-- receives CVs at each company is not. `internal_note` is kept: it is
-- commercial, not personal, and the sponsor pipeline is worth testing with it
-- — the verification pass below still fails the job if an address turns up in
-- one.
UPDATE public.sponsors SET
    cv_contact_email = CASE WHEN cv_contact_email IS NULL THEN NULL
                            ELSE 'sponsor' || id || '@staging.invalid' END,
    cv_contact_name  = CASE WHEN cv_contact_name IS NULL THEN NULL
                            ELSE 'Sponsor Contact' END,
    cv_contact_note  = NULL;

-- Locker grants are keyed by college shortcode, which identifies a person.
UPDATE public.locker_grants SET
    shortcode = 'grant' || abs(hashtext(shortcode)),
    note      = NULL;

-- A free-text admin note is the one field anyone might have typed a name or a
-- reason into. Not worth auditing row by row; it is not what staging is for.
UPDATE public.reservations SET note = NULL WHERE note IS NOT NULL;

-- The hash of a guest's browser cookie (migration 032). Not personal, but it
-- is what shows a browser its bookings; there is no reason to copy it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'reservations'
                  AND column_name  = 'visitor_hash') THEN
        UPDATE public.reservations SET visitor_hash = NULL
         WHERE visitor_hash IS NOT NULL;
    END IF;
END $$;
UPDATE public.admins       SET note = NULL WHERE note IS NOT NULL;

-- Uploaded filenames carry names when the upload was a CV.
UPDATE public.uploads SET
    original_name = 'file-' || id || '.' ||
                    coalesce(nullif(regexp_replace(original_name, '^.*\.', ''), ''), 'bin')
    WHERE purpose IS NOT DISTINCT FROM 'cv';

COMMIT;
