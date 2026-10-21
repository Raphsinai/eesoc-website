"""Authenticated opportunities workflow against the isolated Docker test stack.

All mail goes to Mailpit; none of these tests authorise a real bulk send.
"""

from io import BytesIO
from time import monotonic, sleep
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


def own(client, opportunity_id):
    response = client.get("/me/opportunities")
    assert response.status_code == 200, response.text
    return next(r for r in response.json()["opportunities"] if r["id"] == opportunity_id)


def test_submission_review_owner_signoff_final_queue_and_double_submit(
    api, member_client, admin_client, superuser_client, mailpit,
):
    payload = submission()
    with httpx.Client(base_url=str(api.base_url), timeout=10.0) as guest:
        assert guest.post("/opportunities", json=payload).status_code == 401
        assert guest.post("/opportunities/images", files={
            "file": ("poster.png", image_bytes(), "image/png"),
        }).status_code == 401
        assert guest.get("/me/opportunities").status_code == 401

    assert member_client.post("/opportunities/images", files={
        "file": ("bad.svg", b"<svg></svg>", "image/svg+xml"),
    }).status_code == 400
    image = member_client.post("/opportunities/images", files={
        "file": ("poster.png", image_bytes(), "image/png"),
    })
    assert image.status_code in (200, 201), image.text
    image_id = image.json()["id"]

    created = member_client.post("/opportunities", json={
        **payload, "image_ids": [image_id], "submitted_by": -99,
    })
    assert created.status_code in (200, 201), created.text
    ident = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["images"][0]["id"] == image_id
    assert created.json()["contactEmail"] == payload["contact_email"]
    assert created.json()["reviewNotes"] is None
    assert not any(r["id"] == ident for r in api.get("/opportunities").json()["opportunities"])
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert own(member_client, ident)["status"] == "pending"

    overview = admin_client.get("/admin/dashboard")
    assert overview.status_code == 200
    assert overview.json()["opportunitiesAwaitingApproval"] > 0
    assert any(r["id"] == ident for r in overview.json()["pendingOpportunities"])
    assert any(r["id"] == ident for r in admin_client.get("/admin/opportunities").json()["opportunities"])
    assert superuser_client.get("/admin/opportunities").status_code == 200
    assert member_client.get("/admin/opportunities").status_code == 403
    assert member_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved",
    }).status_code == 403

    # Listing approval does not add a bulk campaign yet.
    approved = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved", "notes": "Good fit for the cohort.",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["emailCampaignId"] is None
    public = api.get(f"/opportunities/{ident}")
    assert public.status_code == 200, public.text
    assert public.json()["title"] == payload["title"]
    assert "reviewNotes" not in public.json()
    assert "announcementBody" not in public.json()
    assert "submittedBy" not in public.json()
    mine = own(member_client, ident)
    assert mine["status"] == "approved"
    assert mine["announcementStatus"] == "draft"
    assert "View this opportunity" in mine["announcementBody"]
    assert mine["emailCampaignId"] is None
    assert not any(c["subject"] == f"Opportunity: {payload['title']}" for c in
                   admin_client.get("/admin/email/queue").json()["items"])

    email_payload = {
        "subject": f"Students: {payload['title']}",
        "body": "**Applications open!**\n\nStudents can join through the attached official programme link.",
    }
    outsider = superuser_client.put(
        f"/me/opportunities/{ident}/announcement", json=email_payload,
    )
    assert outsider.status_code == 409
    draft = member_client.put(
        f"/me/opportunities/{ident}/announcement", json=email_payload,
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["announcementBody"] == email_payload["body"]
    assert draft.json()["emailCampaignId"] is None
    queued = member_client.post(f"/me/opportunities/{ident}/announcement/submit")
    assert queued.status_code == 200, queued.text
    campaign_id = queued.json()["emailCampaignId"]
    assert isinstance(campaign_id, int)
    assert own(member_client, ident)["announcementStatus"] == "awaiting_committee"
    assert member_client.post(f"/me/opportunities/{ident}/announcement/submit").status_code == 409
    assert member_client.put(f"/me/opportunities/{ident}/announcement", json=email_payload).status_code == 409
    assert superuser_client.post(f"/me/opportunities/{ident}/announcement/submit").status_code == 404
    assert admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved",
    }).status_code == 409

    queue = admin_client.get("/admin/email/queue")
    assert queue.status_code == 200, queue.text
    queued_item = next(r for r in queue.json()["items"] if r["id"] == campaign_id)
    assert queued_item["kind"] == "opportunity_announcement"
    assert queued_item["status"] == "awaiting_approval"
    assert queued_item["subject"] == email_payload["subject"]
    details = admin_client.get(f"/admin/email/queue/{campaign_id}")
    assert details.status_code == 200, details.text
    assert details.json()["body"] == email_payload["body"]
    assert set(["dept_ee1", "dept_ee3", "dept_eie3", "dept_msc"]) == set(details.json()["audiences"])
    assert len([r for r in queue.json()["items"] if r["id"] == campaign_id]) == 1
    assert admin_client.post(f"/admin/email/queue/{campaign_id}/dismiss").status_code == 200
    # No bulk send; the committee must separately approve the content.


def test_rejection_notes_edit_permission_and_resubmission(api, member_client, admin_client, superuser_client):
    payload = submission()
    created = member_client.post("/opportunities", json=payload)
    assert created.status_code in (200, 201), created.text
    ident = created.json()["id"]

    rejected = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "rejected", "notes": "Missing the organiser's details; please resubmit.",
        "allow_edit": True,
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["emailCampaignId"] is None
    own_row = own(member_client, ident)
    assert own_row["status"] == "rejected"
    assert own_row["allowEdit"] is True
    assert "please resubmit" in own_row["reviewNotes"]
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert superuser_client.put(f"/me/opportunities/{ident}", json=payload).status_code == 409

    revised = {**payload, "title": f"Updated {payload['title']}",
               "description": "New eligibility details and a fuller explanation of the organising research group."}
    updated = member_client.put(f"/me/opportunities/{ident}", json=revised)
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == ident
    assert updated.json()["status"] == "pending"
    assert updated.json()["allowEdit"] is False
    assert updated.json()["revision"] == 2
    assert updated.json()["title"] == revised["title"]
    assert updated.json()["reviewNotes"] == "Missing the organiser's details; please resubmit."
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert member_client.put(f"/me/opportunities/{ident}", json=revised).status_code == 409
    assert admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved",
    }).status_code == 200

    closed = member_client.post("/opportunities", json=submission())
    closed_id = closed.json()["id"]
    assert admin_client.post(f"/admin/opportunities/{closed_id}/review", json={
        "decision": "rejected", "notes": "This isn't relevant.", "allow_edit": False,
    }).status_code == 200
    assert own(member_client, closed_id)["allowEdit"] is False
    assert member_client.put(f"/me/opportunities/{closed_id}", json=revised).status_code == 409
    assert admin_client.post(f"/admin/opportunities/{closed_id}/review", json={
        "decision": "approved",
    }).status_code == 409
    assert admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "rejected",
    }).status_code == 409


def test_non_admin_committee_role_cannot_moderate(api, superuser_client):
    email = f"opportunities-events-{uuid4().hex[:9]}@example.com"
    response = superuser_client.post("/admin/admins", json={
        "email": email, "role": "events",
    })
    assert response.status_code in (200, 201), response.text
    sign_in = api.post("/auth/dev-login", json={"email": email})
    assert sign_in.status_code == 200, sign_in.text
    assert sign_in.json()["role"] == "events"
    token = sign_in.cookies.get("session")
    csrf = sign_in.cookies.get("csrf")
    with httpx.Client(base_url=str(api.base_url), timeout=10.0,
                      headers={"Cookie": f"session={token}; csrf={csrf}",
                               "X-CSRF-Token": sign_in.json()["csrf_token"]}) as officer:
        assert officer.get("/admin/dashboard").status_code == 200
        dash = officer.get("/admin/dashboard").json()
        assert dash["pendingOpportunities"] == []
        assert dash["opportunitiesAwaitingApproval"] == 0
        assert officer.get("/admin/opportunities").status_code == 403
        assert officer.post("/admin/opportunities/1/review", json={
            "decision": "approved",
        }).status_code == 403


def test_approved_changes_stay_private_until_review_and_takedown_removes_listing(
    api, member_client, admin_client, superuser_client,
):
    """Exercises JSONB decoding, revision approval and takedown in real Postgres."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    future = (datetime.now(ZoneInfo("Europe/London")).date() + timedelta(days=14)).isoformat()
    created = member_client.post("/opportunities", json=submission(posted_until=future))
    assert created.status_code in (200, 201), created.text
    ident = created.json()["id"]
    reviewed = admin_client.post(f"/admin/opportunities/{ident}/review", json={"decision": "approved"})
    assert reviewed.status_code == 200, reviewed.text
    original = api.get(f"/opportunities/{ident}")
    assert original.status_code == 200, original.text
    original_title = original.json()["title"]
    assert original.json()["postedUntil"] == future

    updated = submission(title=f"Updated {original_title}", posted_until=future,
                         description="The revised programme is now open to a different set of applicants.")
    # The owner gate remains effective even for approved opportunities.
    assert superuser_client.put(f"/me/opportunities/{ident}", json=updated).status_code == 409
    proposed = member_client.put(f"/me/opportunities/{ident}", json=updated)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "approved"
    assert proposed.json()["pendingRevision"]["title"] == updated["title"]
    dashboard = admin_client.get("/admin/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    in_queue = next(r for r in dashboard.json()["pendingOpportunities"] if r["id"] == ident)
    assert in_queue["isRevision"] is True
    assert in_queue["title"] == updated["title"]
    assert member_client.put(f"/me/opportunities/{ident}", json=updated).status_code == 409
    assert api.get(f"/opportunities/{ident}").json()["title"] == original_title
    assert not any(r.get("pendingRevision") for r in api.get("/opportunities").json()["opportunities"])

    # Rejecting a proposed revision must keep the previously approved content.
    rejected = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "rejected", "notes": "Please clarify the contact details."
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["opportunity"]["status"] == "approved"
    assert own(member_client, ident)["pendingRevision"] is None
    assert api.get(f"/opportunities/{ident}").json()["title"] == original_title

    # Accept the next revision; this was the direct JSONB decoding bug path.
    assert member_client.put(f"/me/opportunities/{ident}", json=updated).status_code == 200
    approved_revision = admin_client.post(f"/admin/opportunities/{ident}/review", json={
        "decision": "approved"
    })
    assert approved_revision.status_code == 200, approved_revision.text
    public_updated = api.get(f"/opportunities/{ident}")
    assert public_updated.status_code == 200, public_updated.text
    assert public_updated.json()["title"] == updated["title"]
    assert own(member_client, ident)["pendingRevision"] is None

    # The two independent actions remove published content immediately.
    removed = admin_client.post(f"/admin/opportunities/{ident}/takedown", json={
        "allow_edit": True, "notes": "The advertised position is no longer available."
    })
    assert removed.status_code == 200, removed.text
    assert api.get(f"/opportunities/{ident}").status_code == 404
    mine = own(member_client, ident)
    assert mine["status"] == "rejected"
    assert mine["allowEdit"] is True
    assert member_client.put(f"/me/opportunities/{ident}", json=updated).status_code == 200
    assert api.get(f"/opportunities/{ident}").status_code == 404


def test_owner_can_withdraw_approved_opportunity_and_cancel_pending_revision(
    api, member_client, admin_client, superuser_client,
):
    original = submission()
    created = member_client.post("/opportunities", json=original)
    assert created.status_code in (200, 201), created.text
    ident = created.json()["id"]
    assert admin_client.post(
        f"/admin/opportunities/{ident}/review", json={"decision": "approved"}
    ).status_code == 200
    assert api.get(f"/opportunities/{ident}").status_code == 200

    modified = {**original, "title": f"Revised {original['title']}"}
    saved = member_client.put(f"/me/opportunities/{ident}", json=modified)
    assert saved.status_code == 200, saved.text
    assert saved.json()["pendingRevision"] is not None
    # Other authenticated users cannot take down somebody else's opportunity.
    assert superuser_client.post(f"/me/opportunities/{ident}/withdraw").status_code == 409
    withdrawn = member_client.post(f"/me/opportunities/{ident}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["withdrawnAt"] is not None
    assert withdrawn.json()["pendingRevision"] is None
    assert api.get(f"/opportunities/{ident}").status_code == 404
    assert all(row["id"] != ident for row in api.get("/opportunities").json()["opportunities"])
    assert own(member_client, ident)["status"] == "withdrawn"
    assert member_client.post(f"/me/opportunities/{ident}/withdraw").status_code == 409
    assert member_client.put(f"/me/opportunities/{ident}", json=modified).status_code == 409
    assert admin_client.post(
        f"/admin/opportunities/{ident}/review", json={"decision": "approved"}
    ).status_code == 409
