-- External committee requests to add EESoc's WhatsApp number to their own groups.
-- Only an authorised EESoc committee reviewer can accept an invitation.
CREATE TABLE public.external_society_group_invites (
 id BIGSERIAL PRIMARY KEY,
 society_id BIGINT NOT NULL REFERENCES public.external_societies(id) ON DELETE CASCADE,
 invite_ciphertext TEXT NOT NULL,
 requested_by BIGINT NOT NULL REFERENCES public.users(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','joined','declined','failed')),
 joined_jid TEXT,
 decided_by BIGINT REFERENCES public.users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 decided_at TIMESTAMPTZ
);
