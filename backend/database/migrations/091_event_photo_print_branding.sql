-- Event-aware premium photo prints. Preserve historical print labels when an
-- event is renamed or removed, but let the studio choose the live event name.
ALTER TABLE public.events
  ADD COLUMN IF NOT EXISTS photo_short_name VARCHAR(64);

-- Preserve the complete event title even if it exceeds 100 characters.
ALTER TABLE public.photo_sets ALTER COLUMN title TYPE TEXT;

ALTER TABLE public.photo_sets
  ADD COLUMN IF NOT EXISTS event_id BIGINT REFERENCES public.events(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS print_label VARCHAR(100),
  ADD COLUMN IF NOT EXISTS printed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS photo_sets_printed_idx
  ON public.photo_sets (printed_at DESC NULLS LAST, created_at DESC);
CREATE INDEX IF NOT EXISTS photo_sets_event_idx
  ON public.photo_sets (event_id) WHERE event_id IS NOT NULL;
