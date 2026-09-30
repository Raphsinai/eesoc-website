CREATE TABLE IF NOT EXISTS public.lockers (
    Number BIGINT NOT NULL,
    Floor BIGINT NOT NULL,
    BookedBy BIGINT NOT NULL REFERENCES public.users(id),
    available BOOLEAN NOT NULL DEFAULT true
)