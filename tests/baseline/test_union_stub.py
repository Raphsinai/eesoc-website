"""The api is pointed at the union *stub*, never the real eActivities bridge.

The real bridge is rate-limited per source IP and a breach bans the whole
society, so the test stack must speak only to the canned stub. The stub's
health check admits as much, and its /members feed carries the fixture rows
the other tests depend on.
"""

# The canned member the harness documents; member_client signs in as this
# address and test_events.py asserts its membership via the synced mirror.
STUB_MEMBER_EMAIL = "member1@ic.ac.uk"
STUB_MEMBER_LOGIN = "ab1234"


def test_union_stub_health_reports_stub(union_stub):
    """The bridge behind the api is the stub, full stop.

    If UNION_API_URL pointed at real eActivities, this flag would be absent and
    these tests would be spending the society's API quota.
    """
    response = union_stub.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body.get("ok") is True
    assert body.get("stub") is True


def test_union_stub_serves_canned_members(union_stub):
    """The canned members feed includes the documented member1 fixture.

    This is what test_events.py's membership assertion ultimately depends on:
    if the stub does not serve this row, the sync has nothing to mirror.
    """
    response = union_stub.get("/members")
    assert response.status_code == 200
    members = response.json()
    assert isinstance(members, list)
    assert members, "union stub served an empty members feed"
    assert any(
        m.get("email") == STUB_MEMBER_EMAIL or m.get("login") == STUB_MEMBER_LOGIN
        for m in members
    ), "stub members feed is missing the member1@ic.ac.uk / ab1234 fixture"