-- Self-reported CID is used only as a review hint, never proof of society membership.
ALTER TABLE public.reservations ADD COLUMN IF NOT EXISTS claimed_cid VARCHAR(32);
