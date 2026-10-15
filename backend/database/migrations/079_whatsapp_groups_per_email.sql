-- Which WhatsApp groups an email goes to when it is sent — chosen per email.
--
-- 077 gave an email one switch, "also post on WhatsApp", which meant every
-- group chosen on the Email tab. Groups are now picked for each email (a Year 2
-- event to the Year 2 group, not to the whole community's announcements), and
-- nothing is picked until somebody picks it. A list of group ids ("…@g.us"),
-- each of which must still be enabled on the Email tab when the email goes.
--
-- Expand-only. 077's `whatsapp` column stays, unread: no email was ever sent
-- with it outside staging, where the snapshot scrub clears it.
ALTER TABLE public.email_campaigns
    ADD COLUMN IF NOT EXISTS whatsapp_groups JSONB NOT NULL DEFAULT '[]'::jsonb;
