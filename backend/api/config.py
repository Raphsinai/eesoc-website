"""Typed settings.

`os.environ.get(name, "")` fails late and confusingly — a missing adapter URL
surfaces as an httpx error against a path like "/auth/users" rather than as a
startup failure. Declaring settings without defaults makes the container refuse
to boot instead.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- service wiring (set by compose) ---
    database_adapter_url: str
    union_api_url: str = ""

    # --- access ---
    # The standing superuser. Whoever holds this address is a superuser on every
    # request, whatever the admins table says — so the one account that can hand
    # out access cannot be locked out, demoted by somebody they promoted, or
    # lost to a database restored from before they existed. Changing it is a
    # deploy, which is the point.
    superuser_email: str = "ras124@ic.ac.uk"

    # --- sessions ---
    session_ttl_days: int = 7
    session_cookie: str = "session"
    csrf_cookie: str = "csrf"
    csrf_header: str = "X-CSRF-Token"
    # Safari refuses Secure cookies over http://localhost, so this defaults off
    # for local development. It MUST be true anywhere served over HTTPS.
    cookie_secure: bool = False
    # Literal, not str: Starlette's set_cookie only accepts these three, and a
    # typo in the environment should fail at startup rather than at set-cookie.
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- login codes ---
    login_code_ttl_minutes: int = 10
    login_code_max_attempts: int = 5
    login_code_max_per_hour: int = 5
    login_code_resend_seconds: int = 60
    login_code_digits: int = 6

    # --- email ---
    # Imperial's shared webmaster mailbox, via Exchange Online. Taken from the
    # 2023/24 handover: smtp.office365.com:587, LOGIN auth, TLS required.
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587
    smtp_user: str = "eesocweb@ic.ac.uk"
    smtp_pass: str = ""
    # Office 365 refuses to send unless From matches the authenticated mailbox
    # (or a permitted send-as), so this is not freely choosable.
    smtp_from: str = "EESoc Webmaster <eesocweb@ic.ac.uk>"
    # Port 587 is STARTTLS, not implicit TLS. Off by default would send
    # credentials in the clear on the first command.
    smtp_starttls: bool = True
    # Where replies go. The webmaster mailbox is monitored irregularly; anything
    # a student replies to should reach a person.
    smtp_reply_to: str = "eesoc@imperial.ac.uk"
    smtp_timeout_seconds: int = 20
    # With no SMTP host configured, codes are logged instead of sent so the flow
    # is testable before credentials exist. Never leave this on in production.
    log_codes_when_unconfigured: bool = True
    # Who may actually receive mail. Comma-separated addresses, or "@domain"
    # for a whole domain. Empty means everyone — EXCEPT on staging, where empty
    # means no one: staging sends from the real mailbox, and its newsletter
    # audiences include the department's real year-group lists. Anything not
    # allowed is logged instead of sent, so a sign-in code can still be read
    # from the api log.
    mail_allowlist: str = ""

    # --- publicity mailbox (bulk) ---
    # Anything sent to a list rather than to one person goes from the publicity
    # officer's address, not the webmaster's: a newsletter that invites replies
    # should reach the people who write it, and keeping bulk traffic off the
    # mailbox that carries sign-in codes protects the deliverability of the one
    # that must never be filtered.
    #
    # Each field falls back to its transactional counterpart when blank, so a
    # half-configured publicity profile sends from the webmaster mailbox rather
    # than failing. The one that matters is pub_smtp_pass: with no password of
    # its own we authenticate as the webmaster account and still send *from*
    # the publicity address, which is the normal Exchange arrangement for a
    # shared mailbox (it needs Send As).
    pub_smtp_host: str = ""
    pub_smtp_port: int = 0
    pub_smtp_user: str = ""
    pub_smtp_pass: str = ""
    pub_smtp_from: str = ""
    pub_smtp_reply_to: str = ""
    # Tri-state by hand: "" inherits, "true"/"false" override. A bool could not
    # express "not set" and would silently turn STARTTLS off.
    pub_smtp_starttls: str = ""

    # --- bulk sending ---
    # Bulk mail is one message per batch with everybody BCC'd, not one message
    # per person. Exchange Online caps a message at 500 recipients by default;
    # 400 leaves room for an admin having lowered it a little, and still sends a
    # few hundred members in a single message.
    bulk_batch_size: int = 400
    # Exchange Online throttles a mailbox at roughly 30 messages a minute and
    # 10,000 recipients a day. Batches are spaced by this much, which only
    # matters for an audience large enough to need more than one — and for the
    # per-event alerts, which are still one message each.
    bulk_send_interval_seconds: float = 2.0
    # Refuse a send larger than this without it being split. A society
    # newsletter is in the hundreds; four figures means an audience was picked
    # by accident.
    bulk_max_recipients: int = 5000

    # Signs unsubscribe links, so one works from a mail client with no session
    # and cannot be forged for somebody else's address. Falls back to the SMTP
    # password, which is a stable secret this process must already hold — if
    # there is no mail credential there is no mail, so there are no links to
    # sign. Set it explicitly to keep links working across a password reset.
    email_token_secret: str = ""

    # --- automated reminders ---
    # How long before a hold expires the "last chance" email goes out. 24h by
    # default against a 48h hold: late enough that most people have already
    # paid and will never see it, early enough to still act on.
    purchase_reminder_hours_before: int = 24
    # How often the announcer looks for events that have just gone on sale and
    # holds that are about to lapse. Both are database-only, so this is cheap:
    # it never touches eActivities.
    announce_interval_minutes: int = 10

    # --- guest booking ---
    # Guests have not proved they control the address, so their hold is short:
    # long enough to finish paying, not long enough to squat on a seat. A
    # verified account's hold is the event's own hold_hours (72 by default).
    guest_hold_hours: int = 24
    # How long a seat is held while somebody fills in the guest form. Short by
    # design: it exists to stop an oversell mid-typing, not to reserve a ticket.
    draft_hold_minutes: int = 15

    # --- sync ---
    # The Union's sales feed refreshes overnight, so pull it first thing.
    sync_hour: int = 6
    sync_minute: int = 5
    sync_on_startup: bool = False

    icu_centre_code: str = ""

    # Used to build links in outbound email. Must be the public URL, not an
    # internal service address.
    site_origin: str = "https://eesoc.com"

    # --- maintenance ---
    # Extra features to force off by hand, comma separated. Anything whose
    # dependencies are missing is detected automatically (see
    # services/maintenance.py); this is for breakage the process cannot see.
    # Known keys: login, bookings, lockers, sponsor_portal, admin
    maintenance_features: str = ""
    maintenance_message: str = ""

    # --- development only ---
    # Opens POST /auth/dev-login, which mints a session from an email with no
    # code. This MUST be false anywhere real: it is a complete authentication
    # bypass. Guarded again at startup, which refuses to boot with it enabled
    # unless `environment` is explicitly "development".
    dev_login_enabled: bool = False
    environment: Literal["development", "staging", "production"] = "production"


settings = Settings()
