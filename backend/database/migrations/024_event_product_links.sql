-- One product per event, one line per ticket type.
--
-- This is the shape eActivities actually uses: a product ("Annual Dinner 2027")
-- with lines ("With alcohol", "No alcohol"). Linking the product at event level
-- and a line at ticket-type level mirrors that directly, instead of asking an
-- admin to pick "any line or a specific line" against each ticket separately.

-- At most one event-level product link per event. Two would make matching
-- ambiguous with no way to say which was intended.
CREATE UNIQUE INDEX IF NOT EXISTS product_links_event_key
    ON public.product_links (event_id)
    WHERE purpose = 'event_ticket' AND ticket_type_id IS NULL AND event_id IS NOT NULL;

-- At most one link per ticket type, for the same reason.
DROP INDEX IF EXISTS product_links_ticket_key;
CREATE UNIQUE INDEX IF NOT EXISTS product_links_ticket_key
    ON public.product_links (ticket_type_id)
    WHERE ticket_type_id IS NOT NULL;
