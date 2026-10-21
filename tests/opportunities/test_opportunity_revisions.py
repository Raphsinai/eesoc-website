"""Publication boundaries, approved revisions, expiry and separate takedown grants."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from test_opportunity_unit import ROW, api, models


def run(coro):
    return asyncio.run(coro)


def today():
    return datetime.now(ZoneInfo("Europe/London")).date()


def proposal(**changes):
    return {
        "title": "Updated research opportunity",
        "description": "An updated, sufficiently long opportunity description.",
        "links": ["https://example.com/revised"],
        "contact_email": "new@example.com",
        "contact_phone": None,
        "target_years": ["3"],
        "image_ids": [8],
        "posted_until": (today() + timedelta(days=20)).isoformat(),
        **changes,
    }


def test_public_only_sees_approved_and_unexpired_not_pending_revisions(monkeypatch):
    approved = {**ROW, "status": "approved", "posted_until": today().isoformat(),
                "pending_revision": proposal(), "pending_images": []}
    expired = {**approved, "id": 18, "posted_until": (today() - timedelta(days=1)).isoformat()}
    async def fake_get(path, params=None):
        return [approved, expired] if path == "/opportunities" else (
            approved if path == "/opportunities/17" else expired
        )
    monkeypatch.setattr(api.database, "get", fake_get)
    found = run(api.published_opportunities())["opportunities"]
    assert len(found) == 1
    assert found[0]["title"] == ROW["title"]
    assert found[0]["postedUntil"] == today().isoformat()
    assert "pendingRevision" not in found[0]
    assert "pendingRevisionAt" not in found[0]
    assert run(api.published_opportunity(17))["title"] == ROW["title"]
    with pytest.raises(HTTPException) as err:
        run(api.published_opportunity(18))
    assert err.value.status_code == 404
    private = api._view(approved, private=True)
    assert private["pendingRevision"]["title"] == "Updated research opportunity"


def test_deadline_validation_rejects_past_date():
    base = {
        "title": "Valid opportunity", "description": "A very long description of an opportunity",
        "target_years": ["3"],
    }
    api._validate_posted_until(models.OpportunityCreate(**base))
    api._validate_posted_until(models.OpportunityCreate(**base, posted_until=today()))
    with pytest.raises(HTTPException) as exc:
        api._validate_posted_until(models.OpportunityCreate(
            **base, posted_until=today() - timedelta(days=1)
        ))
    assert exc.value.status_code == 422


def test_owner_approved_edit_uses_owner_scoped_endpoint_and_notifies(monkeypatch):
    sent = []
    async def fake_put(path, body):
        sent.append((path, body))
        return {**ROW, "status": "approved", "pending_revision": proposal(),
                "pending_images": [], "submitted_by": 81}
    monkeypatch.setattr(api.database, "put", fake_put)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", lambda coro: coro.close())
    session = SimpleNamespace(user=SimpleNamespace(id=81))
    result = run(api.resubmit_opportunity(
        17, models.OpportunityCreate(
            title="Updated research opportunity",
            description="An updated, sufficiently long opportunity description.",
            target_years=["3"], posted_until=today() + timedelta(days=20),
            submitted_by=999
        ), session
    ))
    assert sent[0][0] == "/opportunities/17"
    assert sent[0][1]["owner_id"] == 81
    assert result["status"] == "approved"
    assert result["pendingRevision"]["title"] == "Updated research opportunity"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_review_of_changes_keeps_existing_publication_pending_decision(monkeypatch, decision):
    calls = []
    row = {**ROW, "status": "approved", "pending_revision": proposal(),
           "posted_until": None}
    async def fake_get(path, params=None):
        assert path == "/opportunities/17"
        return row
    async def fake_post(path, body):
        calls.append((path, body))
        return {"opportunity": {**ROW, "status": "approved",
                                "pending_revision": None, "review_notes": body.get("notes")},
                "email_campaign_id": None}
    monkeypatch.setattr(api.database, "get", fake_get)
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", lambda coro: coro.close())
    result = run(api.moderate_opportunity(
        17, api.ReviewRequest(decision=decision, notes="Review notes"),
        SimpleNamespace(user=SimpleNamespace(id=101),
                        can=lambda p: p == ("opportunities.approve" if decision == "approved" else "opportunities.reject"))
    ))
    assert result["opportunity"]["status"] == "approved"
    assert calls[0][1]["decision"] == decision
    if decision == "approved":
        assert calls[0][1]["campaign_subject"] == "Opportunity: Updated research opportunity"
        assert "new@example.com" in calls[0][1]["campaign_body"]
    else:
        assert "campaign_subject" not in calls[0][1]


@pytest.mark.parametrize("allow_edit,permission", [
    (False, "opportunities.unpublish"),
    (True, "opportunities.return_for_changes"),
])
def test_takedown_requires_distinct_permission_and_owner_cannot_fake_reviewer(monkeypatch, allow_edit, permission):
    seen = []
    async def fake_post(path, body):
        seen.append((path, body))
        return {**ROW, "status": "rejected", "editable_after_rejection": allow_edit,
                "approved_at": "2026-10-20T12:00:00Z"}
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", lambda coro: coro.close())
    req = api.TakedownRequest(allow_edit=allow_edit, notes="Please fix this opportunity.")
    forbidden = SimpleNamespace(user=SimpleNamespace(id=5), can=lambda p: p != permission)
    with pytest.raises(HTTPException) as exc:
        run(api.takedown_opportunity(17, req, forbidden))
    assert exc.value.status_code == 403
    assert seen == []
    accepted = SimpleNamespace(user=SimpleNamespace(id=101), can=lambda p: p == permission)
    result = run(api.takedown_opportunity(17, req, accepted))
    assert seen[0][0] == "/opportunities/17/takedown"
    assert seen[0][1]["reviewed_by"] == 101
    assert seen[0][1]["allow_edit"] is allow_edit
    assert result["opportunity"]["status"] == "rejected"


def test_return_for_changes_requires_feedback():
    session = SimpleNamespace(user=SimpleNamespace(id=101), can=lambda p: True)
    with pytest.raises(HTTPException) as exc:
        run(api.takedown_opportunity(17, api.TakedownRequest(allow_edit=True), session))
    assert exc.value.status_code == 400


@pytest.mark.parametrize('status,days_ago', [
    ('rejected', 0), ('approved', 1),
])
def test_queue_refuses_announcement_after_takedown_or_expiry(monkeypatch, status, days_ago):
    from services import campaigns
    async def fake_get(path, params=None):
        assert path == '/opportunities/17'
        return {**ROW, 'status': status,
                'posted_until': (today() - timedelta(days=days_ago)).isoformat()}
    monkeypatch.setattr(campaigns.database, 'get', fake_get)
    row = {'kind': 'opportunity_announcement', 'opportunity_id': 17,
           'subject': 'Opportunity', 'body': 'A meaningful announcement'}
    with pytest.raises(campaigns.Unbuildable):
        run(campaigns.message_for(row))


def test_submitter_withdraws_with_verified_owner_and_without_committee_permission(monkeypatch):
    sent = []
    alerts = []
    async def fake_post(path, body):
        sent.append((path, body))
        return {**ROW, "status": "draft", "withdrawn_at": "2026-10-21T16:10:00Z",
                "pending_revision": None}
    monkeypatch.setattr(api.database, "post", fake_post)
    def dispatch(coro):
        alerts.append(coro)
        coro.close()
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", dispatch)
    session = SimpleNamespace(user=SimpleNamespace(id=81))
    row = run(api.withdraw_my_opportunity(17, session))
    assert sent == [("/opportunities/17/withdraw", {"owner_id": 81})]
    assert row["status"] == "draft"
    assert row["withdrawnAt"] == "2026-10-21T16:10:00Z"
    assert len(alerts) == 1
    assert not api._is_public({**ROW, "status": "withdrawn"})


@pytest.mark.parametrize("status", ["withdrawn", "rejected", "pending"])
def test_withdrawn_and_unpublished_listings_are_not_public(status):
    assert not api._is_public({**ROW, "status": status})


def test_submit_draft_requires_owner_and_queues_reapproval(monkeypatch):
    seen = []
    async def fake_get(path, params=None):
        return {**ROW, "status": "draft", "submitted_by": 81, "posted_until": None}
    async def fake_post(path, body):
        seen.append((path, body))
        return {**ROW, "status": "pending", "submitted_by": 81}
    monkeypatch.setattr(api.database, "get", fake_get)
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", lambda coro: coro.close())
    result = run(api.submit_my_draft(17, SimpleNamespace(user=SimpleNamespace(id=81))))
    assert result["status"] == "pending"
    assert seen == [("/opportunities/17/submit", {"owner_id": 81})]
    with pytest.raises(HTTPException) as exc:
        run(api.submit_my_draft(17, SimpleNamespace(user=SimpleNamespace(id=82))))
    assert exc.value.status_code == 409


def test_saving_draft_does_not_send_review_alert(monkeypatch):
    dispatched = []
    seen = []
    async def fake_put(path, body):
        seen.append((path, body))
        return {**ROW, "status": "draft", "submitted_by": 81}
    monkeypatch.setattr(api.database, "put", fake_put)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", dispatched.append)
    payload = models.OpportunityCreate(
        title="Research opportunity", description="Detailed information about the research opportunity",
        target_years=["2"], submitted_by=999
    )
    result = run(api.resubmit_opportunity(
        17, payload, SimpleNamespace(user=SimpleNamespace(id=81))
    ))
    assert result["status"] == "draft"
    assert dispatched == []
    assert seen[0][1]["owner_id"] == 81
