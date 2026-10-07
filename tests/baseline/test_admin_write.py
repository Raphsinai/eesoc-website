"""The committee-only admin write path: create and delete a sponsor.

Sponsors are the simplest create/delete pair behind the `/admin` prefix, so
they stand in for the whole "an authenticated committee session can write"
acceptance criterion. The CSRF wiring is exercised implicitly: without the
matching X-CSRF-Token header the POST and DELETE would both 403.
"""

import uuid


def _unique_slug(prefix: str = "baseline") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def test_admin_can_create_and_delete_sponsor(admin_client):
    slug = _unique_slug("sponsor")
    # The name the UI would show; the slug is the machine key. Both must be
    # unique, hence the uuid.
    name = f"Baseline test sponsor {slug[-6:]}"

    created = admin_client.post(
        "/admin/sponsors",
        json={"slug": slug, "name": name, "active": True},
    )
    assert created.status_code in (200, 201), created.text
    sponsor = created.json()
    assert sponsor["name"] == name
    assert sponsor["slug"] == slug
    assert sponsor["id"] is not None

    # It must appear in the committee's full sponsor list.
    listing = admin_client.get("/admin/sponsors")
    assert listing.status_code == 200
    names = [s["name"] for s in listing.json()["sponsors"]]
    assert name in names

    # Delete it, then confirm it is gone. Independent of the create above so a
    # failed insert cannot leave the deleting step to mask the failure.
    deleted = admin_client.delete(f"/admin/sponsors/{sponsor['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True

    after = admin_client.get("/admin/sponsors").json()["sponsors"]
    assert all(s["id"] != sponsor["id"] for s in after)