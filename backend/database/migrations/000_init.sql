CREATE TABLE IF NOT EXISTS public.migrations (
    Applied_at TIMESTAMP DEFAULT NOW(),
    Number BIGINT UNIQUE,
    Name VARCHAR
);