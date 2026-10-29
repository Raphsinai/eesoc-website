-- Society-specific SUMS mirrors and independently scoped committee access.
CREATE TABLE public.external_societies (
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, centre_code TEXT NOT NULL UNIQUE,
 credential_ciphertext TEXT NOT NULL,
 synced_year TEXT, synced_at TIMESTAMPTZ, sync_error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE public.external_society_members (
 society_id BIGINT NOT NULL REFERENCES public.external_societies(id) ON DELETE CASCADE,
 academic_year TEXT NOT NULL, cid TEXT NOT NULL,
 login TEXT, first_name TEXT, last_name TEXT,
 PRIMARY KEY (society_id, academic_year, cid)
);
CREATE INDEX external_society_members_login_idx
 ON public.external_society_members (society_id, academic_year, lower(login));
CREATE TABLE public.external_society_committee (
 society_id BIGINT NOT NULL REFERENCES public.external_societies(id) ON DELETE CASCADE,
 login TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'Committee',
 managing BOOLEAN NOT NULL DEFAULT FALSE,
 permissions TEXT[] NOT NULL DEFAULT '{}',
 PRIMARY KEY (society_id, login),
 CONSTRAINT society_committee_login_valid CHECK (login ~ '^[a-z][a-z0-9._-]{1,63}$')
);
CREATE TABLE public.external_society_events (
 society_id BIGINT NOT NULL REFERENCES public.external_societies(id) ON DELETE CASCADE,
 event_id BIGINT NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
 PRIMARY KEY (society_id, event_id)
);
ALTER TABLE public.ticket_types ADD COLUMN eligible_society_ids BIGINT[] NOT NULL DEFAULT '{}';
-- A WhatsApp group is denied by default until enabled by an authorised administrator.
CREATE TABLE public.whatsapp_group_settings (
 account_key TEXT NOT NULL DEFAULT 'eesoc', jid TEXT NOT NULL,
 society_id BIGINT REFERENCES public.external_societies(id) ON DELETE SET NULL,
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 eesoc_enabled BOOLEAN NOT NULL DEFAULT FALSE,
 PRIMARY KEY (account_key, jid)
);
CREATE TABLE public.whatsapp_group_eesoc_access (
 account_key TEXT NOT NULL, jid TEXT NOT NULL,
 user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 PRIMARY KEY (account_key, jid, user_id),
 FOREIGN KEY (account_key, jid) REFERENCES public.whatsapp_group_settings(account_key, jid) ON DELETE CASCADE
);
