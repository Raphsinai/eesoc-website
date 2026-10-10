"""Which outages take passkey sign-in down with them.

A passkey needs no email, so the automatic outage — no mail credentials, the
yearly password lapse — must leave it working: that is half of why passkeys
exist here. A person switching `login` off by hand means all sign-in, so that
must take passkeys down too.

`maintenance.unavailable()` reads its settings at import, so each case runs in
a fresh interpreter inside the test stack's api container with the environment
it needs. NEEDS THE COMPOSE TEST STACK.
"""

import json

SCRIPT = (
    "import json; from services import maintenance; "
    "print(json.dumps(sorted(maintenance.unavailable())))"
)


def _down(compose_exec, **env) -> list[str]:
    assignments = [f"{key}={value}" for key, value in env.items()]
    out = compose_exec("api", "env", *assignments, "python", "-c", SCRIPT)
    return json.loads(out.strip().splitlines()[-1])


def test_a_mail_outage_leaves_passkeys_working(compose_exec):
    down = _down(compose_exec, SMTP_HOST="", MAINTENANCE_FEATURES="")
    assert "login" in down
    assert "passkeys" not in down


def test_switching_sign_in_off_by_hand_includes_passkeys(compose_exec):
    down = _down(compose_exec, MAINTENANCE_FEATURES="login")
    assert {"login", "passkeys"} <= set(down)


def test_passkeys_can_be_switched_off_alone(compose_exec):
    down = _down(compose_exec, MAINTENANCE_FEATURES="passkeys")
    assert "passkeys" in down
    assert "login" not in down
