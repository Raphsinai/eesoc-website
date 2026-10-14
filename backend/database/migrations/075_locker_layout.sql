-- The real lockers.
--
-- 012 seeded four placeholder banks (A–D, 1–12) so the map had something to
-- draw. These are the lockers actually on the walls of levels 3–5: blocks of
-- nine, three across and three down, standing side by side from left to right.
--
-- A locker is identified by where it is, not by the number on its door. The
-- door numbers are not unique — level 4's side wall has two blocks both
-- numbered 482–490, level 5's has two numbered 582–590, and one block on
-- level 4 reads 373 374 374 along its top row — so (floor, wall, block, slot)
-- is the key and `number` is only what is printed on the door. The numbers are
-- recorded as they were read off the walls on 2026-10-14; a relabel is an
-- UPDATE of `number`, and moves nobody's booking.
--
-- Expand only: `bank` stays, nullable and unset for these rows, so a pod still
-- running the old code reads a shape it understands for the length of the
-- rollout. Dropping it, and making the position columns NOT NULL, is a contract
-- step for a later deploy.

ALTER TABLE public.lockers ADD COLUMN IF NOT EXISTS wall  VARCHAR;  -- front | side
ALTER TABLE public.lockers ADD COLUMN IF NOT EXISTS block INTEGER;  -- 1 = leftmost on that wall
ALTER TABLE public.lockers ADD COLUMN IF NOT EXISTS slot  INTEGER;  -- 1–9: left to right, top row first

ALTER TABLE public.lockers ALTER COLUMN bank DROP NOT NULL;

ALTER TABLE public.lockers DROP CONSTRAINT IF EXISTS lockers_wall_check;
ALTER TABLE public.lockers
    ADD CONSTRAINT lockers_wall_check CHECK (wall IN ('front', 'side'));
ALTER TABLE public.lockers DROP CONSTRAINT IF EXISTS lockers_slot_check;
ALTER TABLE public.lockers
    ADD CONSTRAINT lockers_slot_check CHECK (slot BETWEEN 1 AND 9);

CREATE UNIQUE INDEX IF NOT EXISTS lockers_position_key
    ON public.lockers (floor, wall, block, slot);

-- The placeholders do not exist on any wall. None has ever been booked —
-- booking has been paused in production since lockers shipped — but deleting a
-- locker cascades to its bookings, so check rather than assume: a placeholder
-- somebody holds stops the migration for a person to move by hand.
DELETE FROM public.lockers l
 WHERE l.wall IS NULL
   AND NOT EXISTS (SELECT 1 FROM public.locker_bookings b WHERE b.locker_id = l.id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.lockers WHERE wall IS NULL) THEN
        RAISE EXCEPTION 'A placeholder locker from 012 has a booking. Move it to a real locker by hand, then let 075 run.';
    END IF;
END $$;

-- One row per block, listed left to right as it stands on the wall, with the
-- number on its top-left door. The nine doors run on from there, left to
-- right along each row.
INSERT INTO public.lockers (floor, wall, block, slot, number)
SELECT b.floor, b.wall, b.block, s.slot, b.first_number + s.slot - 1
  FROM (VALUES
        -- Level 3: the front wall only.
        (3, 'front', 1, 301), (3, 'front', 2, 310), (3, 'front', 3, 319),
        (3, 'front', 4, 328), (3, 'front', 5, 337), (3, 'front', 6, 346),
        -- Level 4.
        (4, 'front', 1, 401), (4, 'front', 2, 410), (4, 'front', 3, 419),
        (4, 'front', 4, 428), (4, 'front', 5, 437), (4, 'front', 6, 446),
        (4, 'side',  1, 464), (4, 'side',  2, 473), (4, 'side',  3, 482),
        (4, 'side',  4, 482), (4, 'side',  5, 373), (4, 'side',  6, 364),
        -- Level 5.
        (5, 'front', 1, 501), (5, 'front', 2, 510), (5, 'front', 3, 519),
        (5, 'front', 4, 528), (5, 'front', 5, 537), (5, 'front', 6, 546),
        (5, 'side',  1, 555), (5, 'side',  2, 564), (5, 'side',  3, 582),
        (5, 'side',  4, 582)
       ) AS b(floor, wall, block, first_number),
       generate_series(1, 9) AS s(slot)
ON CONFLICT (floor, wall, block, slot) DO NOTHING;

-- The one door that breaks its block's run: the top right of level 4's fifth
-- side-wall block reads 374, the same as its neighbour, where 375 would be.
UPDATE public.lockers SET number = 374
 WHERE floor = 4 AND wall = 'side' AND block = 5 AND slot = 3;
