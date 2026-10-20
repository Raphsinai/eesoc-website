-- NULL: follow the role preset. An empty array: explicitly no permissions.
-- Per-person overrides survive role label changes and are resolved at request time.
ALTER TABLE public.admins ADD COLUMN IF NOT EXISTS permissions TEXT[] DEFAULT NULL;
