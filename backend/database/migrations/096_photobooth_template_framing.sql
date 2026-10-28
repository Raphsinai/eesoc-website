-- Deterministic, editable print framing and the supplied postcard design.
-- Keep full-resolution uploads unchanged; only snapshot rendering metadata.
ALTER TABLE public.photo_sets
  ADD COLUMN IF NOT EXISTS placements JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS event_date TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS event_venue TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS qr_code VARCHAR(16);
CREATE UNIQUE INDEX IF NOT EXISTS photo_sets_qr_code_unique
  ON public.photo_sets (qr_code) WHERE qr_code IS NOT NULL;

-- Make the edit available to existing administrative role presets; individual
-- committee grants remain separately revocable.
UPDATE public.committee_roles
   SET capabilities = array_append(capabilities, 'photos.framing.edit')
 WHERE id IN ('superuser', 'admin')
   AND NOT ('photos.framing.edit' = ANY(capabilities));
