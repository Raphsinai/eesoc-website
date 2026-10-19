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


def test_approval_queues_but_never_sends_without_email_review(monkeypatch):
    created = []

    async def fake_get(path, params=None):
        if path == "/opportunities/17":
            return {**ROW, "status": "pending"}
        if path == "/email/campaigns/123":
            return {"id": 123, "status": "awaiting_approval", "audiences": ["dept_msc"]}
        raise AssertionError(path)

    async def fake_post(path, body):
        created.append((path, body))
        return {"opportunity": {**ROW, "status": "approved", "email_campaign_id": 123},
                "email_campaign_id": 123}

    async def fake_recipients(_campaign):
        return ([{"email": "list@ic.ac.uk"}], [])

    async def fake_patch(path, params):
        assert path == "/email/campaigns/123" and params == {"recipients": 1}
        return {"id": 123}

    async def fake_notify(_campaign):
        return None

    monkeypatch.setattr(api.database, "get", fake_get)
    monkeypatch.setattr(api.database, "post", fake_post)
    monkeypatch.setattr(api.database, "patch", fake_patch)
    monkeypatch.setattr(api.campaigns, "recipients_for", fake_recipients)
    monkeypatch.setattr(api.campaigns, "_notify", fake_notify)

    response = asyncio.run(api.moderate_opportunity(
        17, api.ReviewRequest(decision="approved"),
        SimpleNamespace(user=SimpleNamespace(id=101)),
    ))
    assert response["emailCampaignId"] == 123
    assert created[0][1]["reviewed_by"] == 101
    assert created[0][1]["campaign_audiences"] == [
        "dept_ee2", "dept_eie2", "dept_ee3", "dept_eie3", "dept_msc",
    ]
    assert not any("send" in path for path, _ in created)
