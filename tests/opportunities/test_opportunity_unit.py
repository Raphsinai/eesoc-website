"""Validation and permission-shaping for community opportunities, no containers."""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:9")

API = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

api = importlib.import_module("routes.opportunities")
models = importlib.import_module("models.opportunities")


ROW = {
    "id": 17,
    "title": "Summer microelectronics research",
    "description": "A meaningful description with eligibility and information.",
    "links": ["https://example.com/apply"],
    "target_years": ["2", "3", "msc"],
    "images": [{"id": 8, "stored_name": "aa/secret.webp", "content_type": "image/webp"}],
    "contact_email": "research@example.org",
    "contact_phone": "+44 20 1234 5678",
    "submitted_by": 81,
    "submitter_email": "submitter@example.org",
    "status": "rejected",
    "review_notes": "Please add eligibility details.",
    "reviewed_at": "2026-10-19T22:00:00Z",
    "email_campaign_id": None,
    "created_at": "2026-10-19T21:00:00Z",
    "approved_at": None,
}


def payload(**changes):
    return {
        "title": ROW["title"],
        "description": ROW["description"],
        "target_years": ["2", "3"],
        **changes,
    }


def test_schema_validates_http_links_target_years_images_phone_and_required_content():
    for change in (
        {"links": ["javascript:alert(1)"]},
        {"target_years": ["2", "2"]},
        {"target_years": ["5"]},
        {"target_years": []},
        {"image_ids": [1, 1]},
        {"image_ids": [1, 2, 3, 4, 5, 6]},
        {"contact_phone": "<script>"},
        {"title": "A"},
        {"description": "short"},
    ):
        with pytest.raises(ValidationError):
            models.OpportunityCreate(**payload(**change))
    valid = models.OpportunityCreate(**payload(links=["https://example.com"], contact_phone="+44 20 1234 5678"))
    assert valid.links[0].scheme == "https"


def test_public_view_never_leaks_submitter_status_or_review_notes():
    public = api._view(ROW)
    assert public["title"] == ROW["title"]
    assert public["images"][0]["url"].endswith("aa/secret.webp")
    for key in ("submittedBy", "reviewNotes", "reviewedAt", "status", "emailCampaignId"):
        assert key not in public
    own = api._view(ROW, private=True)
    assert own["status"] == "rejected"
    assert own["reviewNotes"] == "Please add eligibility details."
    assert "submittedBy" not in own
    admin = api._view(ROW, committee=True)
    assert admin["submittedBy"] == "submitter@example.org"


def test_announcement_targets_both_eee_eie_years_and_has_link(monkeypatch):
    monkeypatch.setattr(api.settings, "site_origin", "https://eesoc.com")
    subject, body, groups = api._announcement({**ROW, "status": "pending"})
    assert subject == f"Opportunity: {ROW['title']}"
    assert "https://eesoc.com/opportunities/17" in body
    assert "research@example.org" in body
    assert "Year 2, Year 3, MSc" in body
    assert groups == ["dept_ee2", "dept_eie2", "dept_ee3", "dept_eie3", "dept_msc"]


def test_submission_ignores_forged_submitted_by(monkeypatch):
    seen = {}

    async def fake_post(path, data):
        seen.update(data)
        return {**ROW, **data, "status": "pending"}

    monkeypatch.setattr(api.database, "post", fake_post)
    session = SimpleNamespace(user=SimpleNamespace(id=81))
    submitted = asyncio.run(api.submit_opportunity(
        models.OpportunityCreate(**payload(submitted_by=999)), session
    ))
    assert seen["submitted_by"] == 81
    assert submitted["status"] == "pending"


def test_public_list_is_filtered_and_my_list_is_owner_scoped(monkeypatch):
    async def fake_get(path, params=None):
        assert path == "/opportunities"
        if params.get("status") == "approved":
            return [{**ROW, "status": "approved"}, ROW]
        return [{**ROW, "submitted_by": 81}, {**ROW, "id": 18, "submitted_by": 99}]

    monkeypatch.setattr(api.database, "get", fake_get)
    public = asyncio.run(api.published_opportunities())
    assert len(public["opportunities"]) == 1
    private = asyncio.run(api.my_opportunities(SimpleNamespace(user=SimpleNamespace(id=81))))
    assert len(private["opportunities"]) == 1
    assert private["opportunities"][0]["status"] == "rejected"


def test_review_prepares_draft_without_queueing_or_sending(monkeypatch):
    created = []
    notices = []

    async def fake_get(path, params=None):
        assert path == "/opportunities/17"
        return {**ROW, "status": "pending"}

    async def fake_post(path, body):
        created.append((path, body))
        return {"opportunity": {
            **ROW, "status": "approved", "email_campaign_id": None,
            "announcement_subject": body["campaign_subject"],
            "announcement_body": body["campaign_body"],
        }, "email_campaign_id": None}

    def dispatch(coro):
        # The production dispatcher creates a task; unit tests must not
        # outlive their asyncio.run loop or accidentally email somebody.
        notices.append(coro)
        coro.close()

    monkeypatch.setattr(api.database, "get", fake_get)
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", dispatch)

    response = asyncio.run(api.moderate_opportunity(
        17, api.ReviewRequest(decision="approved"),
        SimpleNamespace(user=SimpleNamespace(id=101), can=lambda p: p == "opportunities.approve"),
    ))
    assert response["emailCampaignId"] is None
    assert response["opportunity"]["announcementStatus"] == "draft"
    assert created[0][1]["reviewed_by"] == 101
    assert created[0][1]["campaign_subject"] == f"Opportunity: {ROW['title']}"
    assert "View this opportunity" in created[0][1]["campaign_body"]
    assert len(notices) == 1
    assert all("/email/campaigns" not in path for path, _ in created)


def test_member_signoff_is_required_before_email_approval_queue(monkeypatch):
    written = []
    alerts = []

    async def fake_get(path, params=None):
        if path == "/opportunities/17":
            return {**ROW, "status": "approved", "email_campaign_id": None}
        if path == "/email/campaigns/123":
            return {"id": 123, "status": "awaiting_approval", "audiences": ["dept_msc"]}
        raise AssertionError(path)

    async def fake_post(path, body):
        written.append((path, body))
        return {"email_campaign_id": 123}

    async def fake_patch(path, params):
        return {"id": 123}

    async def fake_recipients(_campaign):
        return ([{"email": "list@ic.ac.uk"}], [])

    def dispatch(coro):
        alerts.append(coro)
        coro.close()

    monkeypatch.setattr(api.database, "get", fake_get)
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.database, "patch", fake_patch)
    monkeypatch.setattr(api.campaigns, "recipients_for", fake_recipients)
    monkeypatch.setattr(api.opportunity_notifications, "dispatch", dispatch)

    result = asyncio.run(api.submit_announcement(
        17, SimpleNamespace(user=SimpleNamespace(id=81)),
    ))
    assert result["emailCampaignId"] == 123
    assert written == [("/opportunities/17/announcement/submit", {
        "owner_id": 81,
        "audiences": ["dept_ee2", "dept_eie2", "dept_ee3", "dept_eie3", "dept_msc"],
    })]
    assert len(alerts) == 1


def test_announcement_cannot_be_submitted_by_a_different_owner(monkeypatch):
    from fastapi import HTTPException

    async def fake_get(path):
        return {**ROW, "status": "approved"}

    monkeypatch.setattr(api.database, "get", fake_get)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.submit_announcement(17, SimpleNamespace(user=SimpleNamespace(id=99))))
    assert error.value.status_code == 404


def test_opportunity_admin_guard_uses_explicit_permission_not_role_label():
    from fastapi import HTTPException
    from utils.deps import require_opportunities_admin

    for role in ("events", "industry", "admin", None):
        session = SimpleNamespace(role=role, is_admin=True, can=lambda _: False)
        with pytest.raises(HTTPException) as error:
            asyncio.run(require_opportunities_admin(session))
        assert error.value.status_code == 403
    for role in ("industry", "events", "admin", "superuser"):
        session = SimpleNamespace(role=role, is_admin=True, can=lambda c: c == "opportunities.view")
        assert asyncio.run(require_opportunities_admin(session)) is session


def test_only_users_with_opportunities_permission_are_notified(monkeypatch):
    from services import opportunity_notifications as alerts

    async def fake_get(path):
        if path == '/committee-roles':
            return [
                {'id': 'events', 'capabilities': []},
                {'id': 'industry', 'capabilities': []},
                {'id': 'admin', 'capabilities': ['opportunities.view']},
                {'id': 'superuser', 'capabilities': ['opportunities.view']},
            ]
        assert path == '/auth/admins'
        return [
            {'role': 'events', 'email': 'events@example.org'},
            {'role': 'industry', 'email': 'industry@example.org'},
            {'role': 'admin', 'email': 'admin@example.org'},
            {'role': 'superuser', 'email': 'senior@example.org'},
            {'role': 'admin', 'email': 'ADMIN@example.org'},
            # Explicit per-person permissions take precedence over role defaults.
            {'role': 'admin', 'email': 'revoked@example.org', 'permissions': []},
            {'role': 'events', 'email': 'delegated@example.org',
             'permissions': ['opportunities.view']},
        ]

    monkeypatch.setattr(alerts.database, 'get', fake_get)
    monkeypatch.setattr(alerts.settings, 'superuser_email', 'standing@example.org')
    recipients = asyncio.run(alerts.admin_recipients())
    assert recipients == [
        'admin@example.org', 'delegated@example.org', 'senior@example.org',
        'standing@example.org',
    ]


def test_notifications_reach_current_admins_and_submitter_not_public_contact(monkeypatch):
    from services import opportunity_notifications as alerts

    sent = []

    async def fake_get(path):
        if path == '/committee-roles':
            return [
                {'id': 'events', 'capabilities': []},
                {'id': 'admin', 'capabilities': ['opportunities.view']},
            ]
        assert path == '/auth/admins'
        return [
            {'role': 'events', 'email': 'events@example.org'},
            {'role': 'admin', 'email': 'admin@example.org'},
        ]

    async def fake_send_many(to, subject, text):
        sent.append((tuple(to), subject, text))
        return to

    async def fake_send(to, subject, text):
        sent.append(((to,), subject, text))
        return True

    monkeypatch.setattr(alerts.database, "get", fake_get)
    monkeypatch.setattr(alerts.settings, "superuser_email", "superuser@example.org")
    monkeypatch.setattr(alerts.mailer, "send_many", fake_send_many)
    monkeypatch.setattr(alerts.mailer, "send", fake_send)

    asyncio.run(alerts.alert_admins(
        "Title", "Review now", "New submission", "https://eesoc.test/admin",
    ))
    assert sent[0][0] == ("admin@example.org", "superuser@example.org")
    assert "events@example.org" not in str(sent[0])

    asyncio.run(alerts.alert_submitter({
        **ROW, "status": "rejected", "editable_after_rejection": True,
    }))
    assert sent[1][0] == ("submitter@example.org",)
    assert "Please add eligibility details." in sent[1][2]
    assert "edit it and resubmit" in sent[1][2]
    assert "research@example.org" not in str(sent[1])
