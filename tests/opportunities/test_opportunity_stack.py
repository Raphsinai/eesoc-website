"""End-to-end opportunity moderation against the isolated compose test stack.

Uses ordinary member and admin sessions with real Postgres, real uploads, and
existing email approvals. No send is authorised by these tests.
"""

from io import BytesIO
from uuid import uuid4

import httpx

from PIL import Image


def submission(**changes):
    stamp = uuid4().hex[:10]
    return {
        "title": f"Research opportunity {stamp}",
        "description": "A research programme open to EEE and EIE students, with an application and more information.",
        "links": ["https://example.com/research"],
        "contact_email": "research@example.org",
        "contact_phone": "+44 20 1234 5678",
        "target_years": ["1", "3", "msc"],
        **changes,
    }


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (12, 12)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_member_opportunity_requires_login_is_private_until_approval_and_queues_mail(api, member_client, admin_client):
    payload = submission()
    # The shared `api` fixture acquires cookies when member_client signs in,
    # so only a fresh client is genuinely anonymous. Without that isolation
    # this assertion may accidentally exercise a signed-in session.
    with httpx.Client(base_url=str(api.base_url), timeout=10.0) as guest:
        assert guest.post("/opportunities", json=payload).status_code == 401
        assert guest.post("/opportunities/images", files={
            "file": ("poster.png", image_bytes(), "image/png"),
        }).status_code == 401
        assert guest.get("/me/opportunities").status_code == 401

    bad = member_client.post("/opportunities/images", files={
        "file": ("bad.svg", b"<svg></svg>", "image/svg+xml"),
    })
    assert bad.status_code == 400
    image = member_client.post("/opportunities/images", files={
        "file": ("poster.png", image_bytes(), "image/png"),
    })
    assert image.status_code in (200, 201), image.text
    image_id = image.json()["id"]

    created = member_client.post("/opportunities", json={
        **payload, "image_ids": [image_id], "submitted_by": -99,
    })
    assert created.status_code in (200, 201), created.text
    row = created.json()
    ident = row["id"]
    assert row["status"] == "pending"
    assert row["images"][0]["id"] == image_id
    assert row["contactEmail"] == payload["contact_email"]
    assert row["reviewNotes"] is None

    assert not any(r["id"] == ident for r in api.get("/opportunities").json()["opportunities"])
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert any(r["id"] == ident for r in member_client.get("/me/opportunities").json()["opportunities"])
    assert admin_client.get("/admin/opportunities").status_code == 200
    assert any(r["id"] == ident for r in admin_client.get("/admin/opportunities").json()["opportunities"])
    assert member_client.get("/admin/opportunities").status_code == 403
    assert member_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved",
    }).status_code == 403

    approved = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved", "notes": "Good fit for the cohort.",
    })
    assert approved.status_code == 200, approved.text
    draft_id = approved.json()["emailCampaignId"]
    assert isinstance(draft_id, int)
    public = api.get(f"/opportunities/{ident}")
    assert public.status_code == 200, public.text
    assert public.json()["title"] == payload["title"]
    assert "reviewNotes" not in public.json()
    assert "submittedBy" not in public.json()
    assert any(r["id"] == ident for r in api.get("/opportunities").json()["opportunities"])
    assert next(r for r in member_client.get("/me/opportunities").json()["opportunities"] if r["id"] == ident)["status"] == "approved"

    queue = admin_client.get("/admin/email/queue")
    assert queue.status_code == 200, queue.text
    queued = next(r for r in queue.json()["items"] if r["id"] == draft_id)
    assert queued["kind"] == "opportunity_announcement"
    assert queued["status"] == "awaiting_approval"
    details = admin_client.get(f"/admin/email/queue/{draft_id}")
    assert details.status_code == 200, details.text
    assert "View this opportunity" in details.json()["body"]
    assert set(["dept_ee1", "dept_ee3", "dept_eie3", "dept_msc"]) == set(details.json()["audiences"])

    again = admin_client.post(f"/admin/opportunities/{ident}/review", json={"decision": "approved"})
    assert again.status_code == 409
    queue_again = admin_client.get("/admin/email/queue").json()["items"]
    assert len([r for r in queue_again if r["id"] == draft_id]) == 1
    # No send — the committee must separately approve this email.
    assert admin_client.post(f"/admin/email/queue/{draft_id}/dismiss").status_code == 200


def test_rejection_notes_visible_to_submitter_not_to_public(api, member_client, admin_client):
    payload = submission()
    created = member_client.post("/opportunities", json=payload)
    assert created.status_code in (200, 201), created.text
    ident = created.json()["id"]

    rejected = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "rejected", "notes": "Missing the organiser's details; please resubmit.",
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["emailCampaignId"] is None
    own = next(r for r in member_client.get("/me/opportunities").json()["opportunities"] if r["id"] == ident)
    assert own["status"] == "rejected"
    assert "please resubmit" in own["reviewNotes"]
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert admin_client.post(f"/admin/opportunities/{ident}/review", json={"decision": "approved"}).status_code == 409
