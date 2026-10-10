-- Prove the scrub worked, before the snapshot is allowed out of the cluster.
--
-- scrub.sql is a list of columns somebody wrote down. This is not: it walks
-- every text column of every table in information_schema and fails if any of
-- them still holds something that looks like a real person. That is the whole
-- point — the next migration to add an email column will be caught here even
-- though nobody remembered to add it to scrub.sql, and the snapshot job fails
-- loudly instead of quietly shipping it to staging.
--
-- Raises an exception on the first problem, which aborts the job. A snapshot
-- that cannot be proven clean is never uploaded.

DO $$
DECLARE
    col      record;
    n        bigint;
    problems text[] := '{}';
BEGIN
    FOR col IN
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('character varying', 'text', 'character')
        ORDER BY table_name, column_name
    LOOP
        -- Anything shaped like an email whose domain is not the reserved
        -- .invalid TLD the scrub rewrites to.
        EXECUTE format(
            'SELECT count(*) FROM public.%I
              WHERE %I ~ ''[[:alnum:]._%%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}''
                AND %I !~ ''@[[:alnum:].-]*invalid''',
            col.table_name, col.column_name, col.column_name
        ) INTO n;

        IF n > 0 THEN
            problems := problems ||
                format('%s.%s holds %s real-looking email address(es)',
                       col.table_name, col.column_name, n);
        END IF;

        -- Imperial CIDs are 8 digits. Every CID the scrub writes starts with a
        -- 9, so anything starting 0-8 in a CID-ish column was not rewritten.
        IF col.column_name ILIKE '%cid%' THEN
            EXECUTE format(
                'SELECT count(*) FROM public.%I WHERE %I ~ ''\m[0-8][0-9]{7}\M''',
                col.table_name, col.column_name
            ) INTO n;

            IF n > 0 THEN
                problems := problems ||
                    format('%s.%s holds %s unscrubbed CID(s)',
                           col.table_name, col.column_name, n);
            END IF;
        END IF;
    END LOOP;

    -- Credential tables must be empty, not merely anonymised: a session row is
    -- a working credential whatever the user's name has been changed to.
    -- Tables a newer migration adds are only checked once they exist: the
    -- snapshot is of production's schema, which lags staging's.
    FOR col IN SELECT unnest(ARRAY['sessions', 'login_codes', 'passkeys',
                                   'passkey_links', 'webauthn_challenges']) AS table_name
    LOOP
        CONTINUE WHEN to_regclass('public.' || col.table_name) IS NULL;
        EXECUTE format('SELECT count(*) FROM public.%I', col.table_name) INTO n;
        IF n > 0 THEN
            problems := problems ||
                format('%s still has %s row(s); it must be empty',
                       col.table_name, n);
        END IF;
    END LOOP;

    IF array_length(problems, 1) > 0 THEN
        RAISE EXCEPTION E'snapshot is NOT clean, refusing to publish it:\n  %',
            array_to_string(problems, E'\n  ');
    END IF;

    RAISE NOTICE 'scrub verified: no real identities left in the snapshot';
END $$;
