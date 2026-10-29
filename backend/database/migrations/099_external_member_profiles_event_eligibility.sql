-- Preserve the complete Union report for authorised internal uses, alongside
-- indexed identity fields. One membership is scoped to one society and year.
ALTER TABLE public.external_society_members
  ADD COLUMN email TEXT,
  ADD COLUMN order_no TEXT,
  ADD COLUMN member_type TEXT,
  ADD COLUMN union_profile JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX external_society_members_email_idx
  ON public.external_society_members (academic_year, lower(email))
  WHERE email IS NOT NULL;
CREATE INDEX external_society_members_cid_idx
  ON public.external_society_members (academic_year, cid);

-- Public events are unchanged by default. A restricted event can accept EESoc,
-- one or more external societies, or external societies with EESoc excluded.
-- Ticket-type restrictions are an additional requirement, not an override.
ALTER TABLE public.events
  ADD COLUMN membership_restricted BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN allow_eesoc_members BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN eligible_society_ids BIGINT[] NOT NULL DEFAULT '{}'::bigint[];
ALTER TABLE public.events
  ADD CONSTRAINT event_restricted_has_eligible_society CHECK (
    NOT membership_restricted OR allow_eesoc_members OR cardinality(eligible_society_ids)>0
  );

-- Organising societies are a separate concern from ticket admission.
-- Existing events remain EESoc events. The existing external_society_events
-- junction is also the authoritative external committee-management assignment.
ALTER TABLE public.events
  ADD COLUMN eesoc_organised BOOLEAN NOT NULL DEFAULT TRUE;
