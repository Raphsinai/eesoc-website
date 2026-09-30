-- Admin users. Deliberately just a foreign key to users: admin-ness is a fact
-- about an account, not a separate identity, so admins log in through the same
-- code flow as everyone else.
CREATE TABLE IF NOT EXISTS public.admins (
    user_id    BIGINT      PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by BIGINT      REFERENCES public.users(id) ON DELETE SET NULL,
    note       VARCHAR
);

-- Editable site copy (committee page intro, etc.) so admins can change wording
-- without a deploy.
CREATE TABLE IF NOT EXISTS public.site_content (
    key        VARCHAR     PRIMARY KEY,
    value      TEXT        NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT      REFERENCES public.users(id) ON DELETE SET NULL
);

INSERT INTO public.site_content (key, value) VALUES
    ('committee.intro', 'The committee is elected each spring and runs everything you see here — events, sponsorship, the resource archive and the lockers. Roles and names come straight from the Union''s records.')
ON CONFLICT (key) DO NOTHING;

-- Per-person editorial overlay on the committee list. ICU owns the name and the
-- post; we own the photo and the blurb. Keyed on CID so it survives a re-sync.
CREATE TABLE IF NOT EXISTS public.committee_profiles (
    cid        VARCHAR     PRIMARY KEY,
    intro      TEXT        NOT NULL DEFAULT '',
    photo_url  VARCHAR,
    sort_order INTEGER     NOT NULL DEFAULT 100,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
