-- Append-only source imports. Raw CSV/XLSX bytes are not retained.
CREATE TABLE IF NOT EXISTS public.product_sales_imports (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_sha256 VARCHAR(64) NOT NULL UNIQUE,
    filename VARCHAR(255) NOT NULL,
    product_id BIGINT NOT NULL,
    event_id BIGINT NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
    ticket_type_id BIGINT NOT NULL REFERENCES public.ticket_types(id) ON DELETE CASCADE,
    uploaded_by BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS product_sales_import_event_idx
    ON public.product_sales_imports(event_id, uploaded_at DESC);
CREATE TABLE IF NOT EXISTS public.product_sales_import_rows (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    import_id BIGINT NOT NULL REFERENCES public.product_sales_imports(id) ON DELETE CASCADE,
    source_row INTEGER NOT NULL,
    source_key VARCHAR(64) NOT NULL,
    cid VARCHAR(40),
    buyer_name VARCHAR(255),
    email VARCHAR(255),
    order_number VARCHAR(100),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 100),
    product_line_id BIGINT,
    ticket_type_id BIGINT NOT NULL REFERENCES public.ticket_types(id),
    status VARCHAR(24) NOT NULL CHECK(status IN
        ('confirmed','review','unreserved','duplicate','ticket_added')),
    matched_on VARCHAR(24),
    suggested_reservation_id BIGINT REFERENCES public.reservations(id) ON DELETE SET NULL,
    suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
    reservation_id BIGINT REFERENCES public.reservations(id) ON DELETE SET NULL,
    reviewed_by BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(import_id, source_row)
);
CREATE INDEX IF NOT EXISTS product_sales_rows_status_idx
    ON public.product_sales_import_rows(status, ticket_type_id, id);
CREATE INDEX IF NOT EXISTS product_sales_rows_key_idx
    ON public.product_sales_import_rows(source_key);
