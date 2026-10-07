-- A short list of people to send a test to before the real thing.
--
-- Its own audience ("Test list") in the newsletter and event-email screens,
-- editable from the admin Email tab. Deliberately not filtered by opt-outs:
-- it is a list of committee members who asked to see the drafts.
CREATE TABLE IF NOT EXISTS public.email_test_recipients (
    email      VARCHAR     PRIMARY KEY CHECK (email = lower(email)),
    added_by   BIGINT      REFERENCES public.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO public.email_test_recipients (email)
VALUES ('ras124@ic.ac.uk')
ON CONFLICT (email) DO NOTHING;
