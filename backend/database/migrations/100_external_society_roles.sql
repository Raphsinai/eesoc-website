-- Society-owned role presets: isolated from global EESoc committee roles.
CREATE TABLE public.external_society_roles (
  society_id BIGINT NOT NULL REFERENCES public.external_societies(id) ON DELETE CASCADE,
  role_id TEXT NOT NULL,
  title TEXT NOT NULL,
  permissions TEXT[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (society_id, role_id),
  CONSTRAINT society_role_id_valid CHECK (role_id ~ '^[a-z0-9_-]{2,50}$')
);
ALTER TABLE public.external_society_committee
  ADD COLUMN role_id TEXT,
  ALTER COLUMN permissions DROP NOT NULL,
  ALTER COLUMN permissions DROP DEFAULT;
ALTER TABLE public.external_society_committee
  ADD CONSTRAINT society_committee_role_fk
  FOREIGN KEY (society_id, role_id)
  REFERENCES public.external_society_roles(society_id, role_id);
-- Existing explicitly granted permissions stay intact. New committee members
-- can inherit role defaults via NULL permissions, or keep custom grants.
