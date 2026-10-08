-- A map link for the venue and an online-meeting link for the event, plus a
-- flag marking the meeting link private. A sponsor's Zoom link is often only
-- for people who actually booked, so when the flag is set the public event
-- payload withholds the link and only booked attendees and admins receive it.
--
-- None of these columns can hold an email, CID, name or phone number — they
-- are a URL and a boolean — so nothing here needs adding to
-- deploy/snapshot/scrub.sql.
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS map_url TEXT;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS meeting_url TEXT;
ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS meeting_url_private BOOLEAN NOT NULL DEFAULT FALSE;
