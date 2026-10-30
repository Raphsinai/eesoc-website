ALTER TABLE public.product_sales_import_rows DROP CONSTRAINT IF EXISTS product_sales_import_rows_status_check;
ALTER TABLE public.product_sales_import_rows ADD CONSTRAINT product_sales_import_rows_status_check
CHECK (status IN ('confirmed','review','unreserved','duplicate','ticket_added','ignored'));
