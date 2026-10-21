# Manually linking SUMS / Union shop products

The eActivities sales and products API uses product IDs such as **1003023**.
The Union shop page and the Imperial Union dashboard use the shorter **3023**.
As established with the existing Freshers' Barnight product (1002713 -> 2713),
the website converts a manually entered short shop/SUMS ID to the matching
sales-feed product ID by adding 1,000,000. This conversion is **not** applied
to products selected from the synced eActivities feed. Purchase confirmation
continues matching against the full eActivities `union_sales.product_id`.

In **Admin > Events > Edit > Ticket types**, select **SUMS ID / shop URL** under
**Link using**, then enter either the short ID, the full www-s shop URL or the
full eActivities ID. Save using the adjacent **Link** button for each ticket.
The same choice is available for whole-event links and for locker/membership
products in **Admin > Union products**. The entry works before the product
appears in the feed, without spending API quota. No new database table is
needed: the normalized ID is stored in `product_links`, and a null product-line
ID matches any line of that product.

## Halloween x BioEng x BioSoc — 2026

These are the correspondences between the provided Imperial Union dashboard
screenshots and the event/ticket types visible on the public EESoc API on
2026-10-21 (event #11 in both environments):

| Ticket type ID | Website ticket type | Price | SUMS/shop ID | eActivities ID |
| --- | --- | --- | --- | --- |
| 7 | Early Bird - Alcoholic | £17.50 | 3023 | 1003023 |
| 8 | Early Bird - Non-Alcoholic | £12.50 | 3043 | 1003043 |
| 9 | General - Alcoholic | £20.00 | 3049 | 1003049 |
| 10 | General - Non-Alcoholic | £15.00 | 3047 | 1003047 |

These mappings identify which records to link. **This document is not an
instruction to seed the production or staging database automatically**; an
authorized committee admin must apply changes through the event editor.
The mapping is inferred from the already documented eActivities/shop ID scheme
and is pending a matching actual Union sale row for these particular products.

The backend validates manual references before removing an existing link.
Ticket-level links take priority over any event-level fallback when matching
sales, preventing purchase of one ticket type from confirming a sibling.
Direct ticket purchase URLs already have higher priority than derived shop URLs.
