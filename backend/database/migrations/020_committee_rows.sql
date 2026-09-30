-- Committee laid out in explicit rows.
--
-- A single sort_order forces one flowing grid, so the President ends up wherever
-- the column count happens to put them. The committee wants control of the
-- shape: exec on the top row, officers below. `row_number` picks the row and
-- `sort_order` orders within it.
ALTER TABLE public.committee_profiles
    ADD COLUMN IF NOT EXISTS row_number INTEGER NOT NULL DEFAULT 1
        CHECK (row_number >= 1);

CREATE INDEX IF NOT EXISTS committee_profiles_layout_idx
    ON public.committee_profiles (row_number, sort_order);
