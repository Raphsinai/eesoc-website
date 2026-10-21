ALTER TABLE public.opportunities ADD COLUMN IF NOT EXISTS posted_until DATE;
ALTER TABLE public.opportunities ADD COLUMN IF NOT EXISTS pending_revision JSONB;
ALTER TABLE public.opportunities ADD COLUMN IF NOT EXISTS pending_revision_at TIMESTAMPTZ;
