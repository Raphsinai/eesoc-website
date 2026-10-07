"""Development sign-in bypass, and its absence on the second instance.

The test compose project runs one api with DEV_LOGIN_ENABLED=true (for the
Playwright admin storage state and these tests) and a second with it off. The
second instance is the canary: if the bypass ever leaks into a non-development
deployment, this fails.
"""

# A throwaway, not-an-Imperial address. dev-login creates it via the normal
# resolve-or-create path, so it must be a syntactically valid, never-colliding
# address rather than one of the stub union people.
DEV_EMAIL = "dev-login-check@example.com"


def test_dev_login_returns_authenticated_session(api):
    """POST /auth/dev-login mints a working session on the dev instance."""
    response = api.post(
        "/auth/dev-login",
        json={"email": DEV_EMAIL, "make_admin": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    # make_admin grants the "admin" committee role, which the session view
    # flattens into both the roles list and the role field.
    assert "committee" in body["roles"]
    assert body["role"] == "admin" or body["role"] == "superuser"
    assert body["csrf_token"]
    # Session cookies are what authenticate later requests.
    assert api.cookies.get("session")
    assert api.cookies.get("csrf")


def test_dev_login_is_absent_on_second_instance(api_nologin):
    """The DEV_LOGIN_ENABLED=false instance answers 404, not a session."""
    response = api_nologin.post(
        "/auth/dev-login",
        json={"email": DEV_EMAIL, "make_admin": True},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_session_reports_dev_login_disabled(api_nologin):
    """GET /auth/session on the second instance says dev_login is off.

    The SPA hides the dev sign-in panel by reading this flag, so it is the
    value that matters for the UI, not just the 404 above.
    """
    response = api_nologin.get("/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["dev_login"] is False