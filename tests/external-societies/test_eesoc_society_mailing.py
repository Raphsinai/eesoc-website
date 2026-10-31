"""Connected societies can be emailed by the EESoc committee with a distinct grant.

These are fake-roster and no-SMTP tests. External society committee scope must
still be enforced by the service even when EESoc has access to every roster.
"""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:9')
sys.path.insert(0, str(ROOT / 'backend' / 'api'))

admin = importlib.import_module('routes.admin')
approvals = importlib.import_module('routes.approvals')
auth = importlib.import_module('models.auth')
roles = importlib.import_module('utils.roles')
campaigns = importlib.import_module('services.campaigns')
emailing = importlib.import_module('services.emailing')
societies = importlib.import_module('services.societies')
database = importlib.import_module('clients.database')

run = asyncio.run


def actor(*permissions, role='admin'):
    return auth.AuthSession(
        session=auth.Session(
            token_hash='test-token', csrf_token='csrf', user_id=17,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
        user=auth.User(id=17, email='committee@ic.ac.uk', email_verified=True),
        role=role, permissions=list(permissions) if permissions else None,
    )


@pytest.fixture
def roster(monkeypatch):
    async def mailing_list(society_id):
        assert society_id in (3, 4)
        return [
            {'email': 'member@ic.ac.uk', 'first_name': 'A'},
            {'email': f'society{society_id}@ic.ac.uk', 'first_name': 'B'},
        ]
    monkeypatch.setattr(societies, 'mailing_list', mailing_list)
    return mailing_list


def test_superuser_and_permissioned_committee_have_connected_society_grant():
    assert 'societies.members.mail' in roles.capabilities('superuser')
    assert actor(role='superuser').can('societies.members.mail')
    assert actor('email.send', 'societies.members.mail').can('societies.members.mail')
    assert not actor('email.send').can('societies.members.mail')


def test_permissioned_email_composer_exposes_society_lists(monkeypatch, roster):
    async def get(path, params=None):
        assert path == '/external-societies'
        return [{'id': 3, 'name': 'Robotics'}, {'id': 4, 'name': 'Design'}]
    async def empty(*args, **kwargs):
        return []
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(emailing, 'test_recipients', empty)
    monkeypatch.setattr(emailing, 'topics', empty)
    monkeypatch.setattr(emailing, '_resolve_one', lambda key: empty() if not key.startswith('society:') else mailing_one(key, roster))
    monkeypatch.setattr(importlib.import_module('services.whatsapp'), 'available', empty)
    monkeypatch.setattr(admin.mailer, 'describe', lambda: {})

    full = run(admin.email_audiences(actor(role='superuser')))
    by_key = {a['key']: a for a in full['audiences']}
    assert by_key['society:3']['group'] == 'Connected societies'
    assert by_key['society:3']['count'] == 2
    assert by_key['society:4']['count'] == 2
    assert all('society:' not in a['key'] for a in run(admin.email_audiences(actor('email.audiences')))['audiences'])


async def mailing_one(key, mailing_list):
    return await mailing_list(int(key.split(':')[1]))


def test_direct_preview_and_newsletter_send_obey_distinct_grant(monkeypatch, roster):
    dispatched, created = [], []
    async def post(path, data, params=None):
        created.append((path, data))
        return {'id': 43}
    monkeypatch.setattr(database, 'post', post)
    def record_without_sending(coro):
        dispatched.append(coro)
        coro.close()
    monkeypatch.setattr(admin, '_dispatch', record_without_sending)
    monkeypatch.setattr(admin.mailer, 'configured', lambda *args: True)
    # An actual society:3 recipient resolution, with one person belonging to
    # two lists; the mailing service must de-duplicate before dispatch.
    authorised = actor('email.preview', 'email.send', 'societies.members.mail')
    rejected = actor('email.preview', 'email.send')
    keys = ['society:3', 'society:4']
    preview = run(admin.preview_email(body='Hi', audiences=keys, _=authorised))
    assert preview['recipients'] == 3
    assert run(admin.send_newsletter(subject='Hello', body='Hi', heading=None,
                                     audiences=keys, session=authorised))['recipients'] == 3
    assert created[0][1]['audiences'] == keys
    assert len(dispatched) == 1
    with pytest.raises(HTTPException) as err:
        run(admin.preview_email(body='Hi', audiences=['society:3'], _=rejected))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        run(admin.send_newsletter(subject='Hello', body='Hi', heading=None,
                                 audiences=['society:3'], session=rejected))
    assert err.value.status_code == 403
    assert len(created) == 1


def test_queue_rejects_unauthorised_review_edit_send_and_schedule(monkeypatch):
    row = {
        'id': 47, 'status': 'awaiting_approval', 'audiences': ['society:3'],
        'subject': 'Hello', 'body': 'Hi', 'kind': 'newsletter',
        'send_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }
    changed = []
    async def get(path, params=None):
        assert path == '/email/campaigns/47'
        return dict(row)
    async def put(*args, **kwargs):
        changed.append('edit')
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(database, 'put', put)
    ordinary = actor('email.queue.view', 'email.queue.edit', 'email.queue.send', 'email.queue.schedule')
    with pytest.raises(HTTPException) as err:
        run(approvals.review(47, ordinary))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        run(approvals.edit(47, subject='Changed', heading=None, body=None,
            audiences=None, extra=None, excluded=None, _=ordinary))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        run(approvals.send_now(47, ordinary))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        run(approvals.schedule(47, ordinary))
    assert err.value.status_code == 403
    assert changed == []


def test_authorised_queue_reviewer_can_preview_schedule_and_send(monkeypatch, roster):
    row = {
        'id': 47, 'status': 'awaiting_approval', 'audiences': ['society:3'],
        'subject': 'Society notice', 'body': 'Meeting details', 'kind': 'newsletter',
        'send_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }
    decisions, sends = [], []
    async def get(path, params=None):
        if path == '/email/campaigns/47': return dict(row)
        if path == '/whatsapp/posts': return []
        raise AssertionError(path)
    async def decide(row, status, reviewed_by, **kwargs):
        decisions.append((status, reviewed_by, kwargs['recipients']))
        return {**row, 'status': status}
    async def build(row, **kwargs):
        return lambda batch, url: []
    async def send(row, *, expect, reviewed_by, allow_connected_societies):
        sends.append((expect, reviewed_by, allow_connected_societies))
        return 2
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(approvals, '_decide', decide)
    monkeypatch.setattr(campaigns, 'builder_for', build)
    monkeypatch.setattr(campaigns, 'start_send', send)
    monkeypatch.setattr(approvals.mailer, 'configured', lambda *args: True)
    permitted = actor('email.queue.view', 'email.queue.send', 'email.queue.schedule', 'societies.members.mail')

    review = run(approvals.review(47, permitted))
    assert review['count'] == 2
    assert {r['email'] for r in review['recipientList']} == {'member@ic.ac.uk', 'society3@ic.ac.uk'}
    approved = run(approvals.schedule(47, permitted))
    assert approved['ok'] is True
    assert decisions == [('scheduled', 17, 2)]
    sent = run(approvals.send_now(47, permitted))
    assert sent['ok'] is True and sent['recipients'] == 2
    assert sends == [('awaiting_approval', 17, True)]


def test_queue_editor_cannot_add_society_audience_without_permission(monkeypatch):
    async def get(path, params=None):
        assert path == '/email/campaigns/47'
        return {'id': 47, 'status': 'awaiting_approval', 'audiences': ['test']}
    async def must_not_write(*args, **kwargs):
        raise AssertionError('Unauthorized changes must not reach the adapter')
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(database, 'put', must_not_write)
    ordinary = actor('email.queue.edit')
    with pytest.raises(HTTPException) as exc:
        run(approvals.edit(47, subject=None, heading=None, body=None,
                           audiences=['society:3'], extra=None, excluded=None, _=ordinary))
    assert exc.value.status_code == 403


def test_external_society_cannot_use_another_roster_even_when_allow_all(roster):
    forged = {'audiences': ['society-sent:3', 'society:4']}
    with pytest.raises(campaigns.NotSendable) as err:
        run(campaigns.recipients_for(forged, allow_connected_societies=True))
    assert err.value.status == 403
    valid = {'audiences': ['society-sent:3', 'society:3']}
    resolved, _ = run(campaigns.recipients_for(valid, allow_connected_societies=True))
    assert len(resolved) == 2



def test_permissioned_committee_can_choose_society_list_on_sponsor_marketing_review(monkeypatch, roster):
    item = {
        'id': 14, 'status': 'pending', 'sponsor_id': 5,
        'subject': 'Robotics career briefing', 'body': 'An internship to consider.',
        'audiences': ['members'], 'channels': ['email'], 'emailed_at': None,
        'posted_at': None, 'media': [],
    }
    writes, dispatches = [], []
    async def get(path, params=None):
        if path == '/sponsors/marketing/14': return dict(item)
        if path == '/sponsors/5': return {'id': 5, 'name': 'Example Sponsor', 'slug': 'example-sponsor'}
        raise AssertionError(path)
    async def post(path, payload, params=None):
        writes.append((path, payload))
        return {'id': 99}
    async def patch(path, payload, params=None):
        if payload.get('mark_emailed'): return {**item, 'emailed_at': '2026-10-30T13:00:00Z'}
        return {**item, **payload}
    async def available(*, external_societies=False, **kwargs):
        lists=[{'key': 'members', 'label': 'EESoc members'}]
        if external_societies: lists.append({'key': 'society:3', 'label': 'Robotics members'})
        return lists
    def no_send(coro):
        dispatches.append(coro)
        coro.close()
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(database, 'post', post)
    monkeypatch.setattr(database, 'patch', patch)
    monkeypatch.setattr(emailing, 'audiences', available)
    monkeypatch.setattr(admin, '_dispatch', no_send)
    monkeypatch.setattr(admin.mailer, 'configured', lambda *args: True)
    allowed = actor('sponsors.marketing.preview', 'sponsors.marketing.send',
                    'email.audiences', 'email.send', 'societies.members.mail')
    no_society_grant = actor('sponsors.marketing.preview', 'sponsors.marketing.send',
                             'email.audiences', 'email.send')

    preview = run(admin.preview_sponsor_marketing(14, audiences=['society:3'], session=allowed))
    assert preview['recipients'] == 2
    result = run(admin.send_sponsor_marketing(14, whatsapp_groups=[],
                                              audiences=['society:3'], session=allowed))
    assert result['ok'] is True
    assert writes[0][1]['audiences'] == ['society:3', 'sponsor:example-sponsor']
    assert len(dispatches) == 1
    for action in (
        lambda: admin.preview_sponsor_marketing(14, audiences=['society:3'], session=no_society_grant),
        lambda: admin.send_sponsor_marketing(14, whatsapp_groups=[], audiences=['society:3'], session=no_society_grant),
    ):
        with pytest.raises(HTTPException) as exc:
            run(action())
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        run(admin.preview_sponsor_marketing(14, audiences=['society:999'], session=allowed))
    assert exc.value.status_code == 400
    assert len(writes) == 1


def test_superuser_can_add_two_society_rosters_during_queue_review(monkeypatch, roster):
    """Editing the review's recipient picker must update the real count and labels."""
    row = {
        'id': 47, 'status': 'awaiting_approval', 'audiences': ['test'],
        'subject': 'Joint society briefing', 'body': 'Details for members',
        'heading': 'Notice', 'kind': 'newsletter',
    }
    changed = []

    async def get(path, params=None):
        if path == '/email/campaigns/47':
            return dict(row)
        if path == '/whatsapp/posts':
            return []
        raise AssertionError(path)

    async def put(path, data, params=None):
        assert path == '/email/campaigns/47/draft'
        row.update({k: v for k, v in data.items() if v is not None})
        changed.append(dict(data))
        return dict(row)

    async def patch(path, data=None, params=None):
        assert path == '/email/campaigns/47'
        assert params == {'recipients': 3}
        return {'ok': True}

    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(database, 'put', put)
    monkeypatch.setattr(database, 'patch', patch)
    permitted = actor(role='superuser')
    detail = run(approvals.edit(
        47, subject=None, heading=None, body=None,
        audiences=['society:3', 'society:4'], extra=[], excluded=[], _=permitted,
    ))
    assert changed[0]['audiences'] == ['society:3', 'society:4']
    assert detail['count'] == detail['recipients'] == 3
    assert sorted(r['email'] for r in detail['recipientList']) == [
        'member@ic.ac.uk', 'society3@ic.ac.uk', 'society4@ic.ac.uk',
    ]
    assert detail['preview']['subject'] == 'Joint society briefing'
    assert row['status'] == 'awaiting_approval'  # Reviewing never sends.


def test_sponsor_reviewer_has_visible_connected_society_picker():
    source = (ROOT / 'frontend/src/views/AdminSponsorView.vue').read_text()
    assert "'/admin/email/audiences'" in source
    assert "'Connected societies'" in source
    assert 'marketingEmailAudiences' in source
    assert 'toggleMarketingAudience' in source
    assert 'mailFor(m)' in source
