-- Guest booking.
--
-- People can reserve without an account: the form takes an email and, for
-- Imperial students, their shortcode. An account is created for them silently
-- so the booking has an owner, but it is NOT verified — nobody has proved they
-- control that address yet, so such an account must not be treated as a login.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false;

-- Existing accounts all reached us through the code flow, which is proof.
UPDATE public.users SET email_verified = true WHERE last_login_at IS NOT NULL;

-- The Imperial login (local part of the college address). Held even when the
-- person is not in the members feed, because it is how their Union purchase is
-- matched later — an Imperial student who has not joined EESoc still buys
-- tickets under their own shortcode.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS shortcode VARCHAR;

CREATE INDEX IF NOT EXISTS users_shortcode_idx ON public.users (lower(shortcode));

-- Distinguishes a hold made by a guest from one made by a signed-in user, so
-- the door list can show who has actually proved their identity.
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS booked_as_guest BOOLEAN NOT NULL DEFAULT false;
