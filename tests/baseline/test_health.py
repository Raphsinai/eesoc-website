"""Liveness and readiness probes.

These are the two endpoints the compose healthcheck and the load balancer
depend on, so the baseline suite pins their contract before anything else
can be tested against the stack.
"""


def test_health_ok_and_development(api):
    """The public API answers liveness and reports the development environment.

    `/health` must not touch the database (it is the liveness probe), so it is
    a plain ok/environment/academic_year shape and the environment is exactly
    what the test compose project sets.
    """
    response = api.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["environment"] == "development"
    # The academic year is present so the SPA has something to show even when
    # the union feed has not synced yet.
    assert body.get("academic_year")


def test_ready_ok(api):
    """Readiness reports ok because the database and media checks passed.

    Unlike `/health`, `/ready` may return 503 when a dependency is down. The
    test stack starts everything together, so by the time the tests run this
    should be 200 and `ok` true.
    """
    response = api.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # The individual checks are named so a failure says which dependency broke.
    assert body["checks"]["database"] is True