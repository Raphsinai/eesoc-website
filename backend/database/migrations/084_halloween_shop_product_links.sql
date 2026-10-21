-- Link the four Halloween 2026 ticket types to the product ids that appear in
-- the Union/eActivities *sales* feed. The shop/dashboard shows 3023 etc.;
-- eActivities uses the 100xxxx product ids (e.g. 1002713 for shop 2713).
--
-- Do not depend on these products appearing in the products mirror: they are
-- absent from /reports/products until the first sale. The booking matcher
-- joins against the sales feed's product_id and allows a NULL line id.
--
-- This is a guarded one-time backfill, not a general product mapping rule.
-- Use slug + academic year + exact ticket names and prices to avoid modifying
-- an unrelated Halloween event. Never override an existing committee link.
WITH desired(ticket_name, price_pence, union_product_id) AS (
    VALUES
        ('Early Bird - Alcoholic',     1750, 1003023::BIGINT),
        ('Early Bird - Non-Alcoholic', 1250, 1003043::BIGINT),
        ('General - Alcoholic',        2000, 1003049::BIGINT),
        ('General - Non-Alcoholic',    1500, 1003047::BIGINT)
)
INSERT INTO public.product_links (
    purpose, event_id, ticket_type_id, union_product_id,
    union_product_line_id, academic_year
)
SELECT 'event_ticket', e.id, t.id, d.union_product_id, NULL, e.academic_year
  FROM public.events e
  JOIN public.ticket_types t ON t.event_id = e.id
  JOIN desired d ON d.ticket_name = t.name AND d.price_pence = t.price_pence
 WHERE e.slug = 'halloween'
   AND e.academic_year = '26-27'
   AND t.requires_purchase
   AND NOT EXISTS (
       SELECT 1 FROM public.product_links existing
        WHERE existing.ticket_type_id = t.id
   );
