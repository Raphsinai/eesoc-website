"""Sponsor self-service access grants and the isolation of sponsor organisations."""
import uuid

import httpx
import pytest


def signed_in(api, email):
    result = api.post('/auth/dev-login', json={'email': email})
    assert result.status_code == 200, result.text
    return httpx.Client(base_url=str(api.base_url), timeout=10, headers={
        'Cookie': f"session={result.cookies.get('session')}; csrf={result.cookies.get('csrf')}",
        'X-CSRF-Token': result.json()['csrf_token'],
    })


@pytest.fixture
def sponsors(api, admin_client):
    created = []
    clients = []

    def make():
        tag = uuid.uuid4().hex[:12]
        response = admin_client.post('/admin/sponsors', json={
            'slug': f'delegated-{tag}', 'name': f'Delegated {tag}', 'active': False,
        })
        assert response.status_code in (200, 201), response.text
        sponsor = response.json()
        created.append(sponsor['id'])
        email = f'owner-{tag}@example.com'
        assert admin_client.post(f"/admin/sponsors/{sponsor['id']}/users", json={'email': email}).status_code == 200
        client = signed_in(api, email)
        clients.append(client)
        return sponsor, client

    yield make
    for client in clients:
        client.close()
    for sponsor_id in created:
        admin_client.delete(f'/admin/sponsors/{sponsor_id}')


def test_sponsor_can_delegate_and_revoke_without_cross_company_access(api, sponsors, member_client):
    first, owner = sponsors()
    second, competitor = sponsors()
    email = f'delegate-{uuid.uuid4().hex[:12]}@example.com'

    assert member_client.get('/portal/access').status_code == 403
    assert member_client.post('/portal/access', json={'email': email}).status_code == 403
    assert owner.get('/portal/access', params={'sponsor_id': second['id']}).status_code == 403
    assert owner.post('/portal/access', params={'sponsor_id': second['id']}, json={'email': email}).status_code == 403
    assert owner.delete('/portal/access/123', params={'sponsor_id': second['id']}).status_code == 403

    invited = owner.post('/portal/access', json={'email': email})
    assert invited.status_code == 200, invited.text
    assert invited.json()['created'] is True
    user_id = invited.json()['userId']
    assert owner.post('/portal/access', json={'email': email}).json()['created'] is False
    listed = owner.get('/portal/access').json()
    assert {u['email'] for u in listed['users']} == {email, owner.get('/auth/session').json()['user']['email']}
    assert listed['canGrant'] and listed['canRevoke']
    delegate = signed_in(api, email)
    try:
        assert delegate.get('/portal/dashboard').status_code == 200
        assert delegate.get('/portal/dashboard', params={'sponsor_id': second['id']}).status_code == 403
        assert delegate.get('/portal/access', params={'sponsor_id': second['id']}).status_code == 403
        assert delegate.post('/portal/access', params={'sponsor_id': second['id']}, json={'email': 'other@example.com'}).status_code == 403
        assert delegate.delete('/portal/access/1', params={'sponsor_id': second['id']}).status_code == 403
        assert competitor.get('/portal/access').status_code == 200
        assert email not in {u['email'] for u in competitor.get('/portal/access').json()['users']}
        assert delegate.delete(f'/portal/access/{user_id}').status_code == 400
        response = owner.delete(f'/portal/access/{user_id}')
        assert response.status_code == 200, response.text
        assert delegate.get('/portal/dashboard').status_code == 403
        assert delegate.post('/portal/access', json={'email': 'another@example.com'}).status_code == 403
        assert owner.get('/portal/access').status_code == 200
    finally:
        delegate.close()


def test_portal_access_requires_csrf(api, sponsors):
    sponsor, owner = sponsors()
    email = f'csrf-{uuid.uuid4().hex[:10]}@example.com'
    without_csrf = httpx.Client(base_url=str(api.base_url), timeout=10, headers={
        'Cookie': owner.headers['Cookie'],
    })
    try:
        assert without_csrf.post('/portal/access', json={'email': email}).status_code == 403
        assert email not in {u['email'] for u in owner.get('/portal/access').json()['users']}
    finally:
        without_csrf.close()
