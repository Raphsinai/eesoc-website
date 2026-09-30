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
