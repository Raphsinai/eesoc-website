-- Move existing Imperial accounts onto their canonical shortcode@ic.ac.uk form.
--
-- Sign-in and guest booking now reduce every Imperial identity to
-- `shortcode@ic.ac.uk` so one person is one account. Rows created before that
-- still carry whatever address was typed (often firstname.lastname24@…), which
-- would otherwise collide with the canonical form on `users.member_id`, since
-- that column is UNIQUE.
--
-- Only rows whose member record actually gives us a login are touched, and only
-- where the canonical address is not already taken — a genuine clash is left
-- alone for a human to look at rather than silently merged.
UPDATE public.users u
   SET email = m.login || '@ic.ac.uk'
  FROM public.members m
 WHERE u.member_id = m.id
   AND m.login IS NOT NULL
   AND m.login <> ''
   AND lower(u.email) <> lower(m.login || '@ic.ac.uk')
   AND NOT EXISTS (
         SELECT 1 FROM public.users other
          WHERE lower(other.email) = lower(m.login || '@ic.ac.uk')
            AND other.id <> u.id
       );

-- Backfill the shortcode column from the same source.
UPDATE public.users u
   SET shortcode = m.login
  FROM public.members m
 WHERE u.member_id = m.id
   AND u.shortcode IS NULL
   AND m.login IS NOT NULL;
