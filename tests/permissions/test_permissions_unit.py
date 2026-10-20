"""Role defaults, explicit grants, route authorization, and account updates.

No service stack required. Integration tests using Postgres are separate.
"""

import asyncio
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

API = Path(__file__).resolve().parents[2] / 'backend' / 'api'
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:9')
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

roles = importlib.import_module('utils.roles')
models = importlib.import_module('models.auth')
deps = importlib.import_module('utils.deps')
admin = importlib.import_module('routes.admin')
passkeys = importlib.import_module('routes.passkeys')


def make_session(role='events', permissions=None, email='test-access@example.org', user_id=135):
    return models.AuthSession(
        session={
            'token_hash': 'abc', 'csrf_token': 'def', 'user_id': user_id,
            'expires_at': datetime.now(timezone.utc),
        },
        user={'id': user_id, 'email': email},
        role=role, permissions=permissions,
    )


def test_role_presets_preserve_existing_access_with_distinct_actions():
    assert set(roles.ROLES) == {'superuser', 'admin', 'events', 'industry'}
    assert roles.capabilities('superuser') == set(roles.PERMISSIONS)
    assert 'access.grant' not in roles.capabilities('admin')
    assert 'access.view' in roles.capabilities('admin')
    assert 'lockers.allowances.grant' not in roles.capabilities('admin')
    assert {'events.create', 'events.bookings.confirm', 'events.checkin.scan', 'events.sync.union'} <= roles.capabilities('events')
    assert {'sponsors.edit', 'events.scope.industry', 'events.bookings.cancel'} <= roles.capabilities('industry')
    assert not roles.capabilities(None)
    assert not roles.capabilities('unknown')
    assert all(len(spec['label']) and len(spec['description']) for spec in roles.PERMISSIONS.values())
    assert all(c in roles.PERMISSIONS for role in roles.ROLES.values() for c in role['capabilities'])


def test_custom_permissions_replace_not_augment_role_and_empty_means_empty():
    assert roles.effective_capabilities('events', None) == roles.capabilities('events')
    assert roles.effective_capabilities('events', []) == frozenset()
    assert roles.effective_capabilities('events', ['resources', 'email']) == roles.LEGACY_GRANTS['resources'] | roles.LEGACY_GRANTS['email']
    assert roles.effective_capabilities(None, ['users']) == frozenset()
    assert roles.effective_capabilities('admin', ['not-a-permission']) == frozenset()
    assert roles.validate_permissions(['email.send', 'events.create']) == ['email.send', 'events.create']
    assert roles.validate_permissions([]) == []
    assert roles.validate_permissions(None) is None
    for bad in (['whatever'], ['access.grant', 'access.grant'], [17], 'email'):
        with pytest.raises(ValueError):
            roles.validate_permissions(bad)


def test_standing_superuser_cannot_be_narrowed(monkeypatch):
    monkeypatch.setattr(models.settings, 'superuser_email', 'owner@example.org')
    session = make_session('events', [], 'owner@example.org')
    assert session.role == 'superuser'
    assert session.permissions is None
    assert session.capabilities == roles.capabilities('superuser')


def test_permission_checks_are_independent_of_role_label():
    custom = make_session('industry', ['opportunities', 'bookings'])
    assert custom.can('opportunities') and not custom.can('sponsors')
    assert asyncio.run(deps.require_opportunities_admin(custom)) is custom
    assert asyncio.run(deps.require_booking_editor(custom)) is custom
    with pytest.raises(HTTPException) as err:
        asyncio.run(deps.require_event_editor(custom))
    assert err.value.status_code == 403

    restricted = make_session('admin', [])
    for guard in (deps.require_opportunities_admin, deps.require_booking_editor, deps.require_event_editor):
        with pytest.raises(HTTPException) as e:
            asyncio.run(guard(restricted))
        assert e.value.status_code == 403


def test_bookings_scoped_per_event():
    industry = make_session('industry', ['industry_bookings'])
    assert deps.may_manage_booking(industry, {'industry': True})
    assert not deps.may_manage_booking(industry, {'industry': False})
    with pytest.raises(HTTPException) as err:
        deps.assert_may_manage_booking(industry, {'industry': False})
    assert err.value.status_code == 403
    assert deps.may_manage_booking(make_session('events', ['bookings']), {'industry': False})


def test_passkey_link_maker_permission_is_rechecked():
    assert passkeys._link_refusal('admin', 'member@example.org', None, [])
    assert passkeys._link_refusal('industry', 'member@example.org', None, ['passkey_links']) is None
    assert passkeys._link_refusal('industry', 'officer@example.org', 'events', ['passkey_links'])
    assert passkeys._link_refusal('industry', 'officer@example.org', 'events', ['passkey_links', 'users']) is None


def test_api_rejects_unknown_grants_before_account_creation(monkeypatch):
    async def no_create(*args, **kwargs):
        pytest.fail('Must validate before touching accounts')
    monkeypatch.setattr(admin, 'resolve_or_create_user', no_create)
    with pytest.raises(HTTPException) as err:
        asyncio.run(admin.add_admin('x@example.org', 'admin', ['unknown'], make_session('superuser')))
    assert err.value.status_code == 400


def test_api_saves_custom_permissions_and_refuses_self_lockout(monkeypatch):
    recorded = []
    async def create(address, **kwargs):
        return {'id': 135, 'email': address}
    async def send(path, json=None, params=None):
        recorded.append((path, json, params))
    monkeypatch.setattr(admin, 'resolve_or_create_user', create)
    monkeypatch.setattr(admin.database, 'post', send)
    owner = make_session('superuser')
    with pytest.raises(HTTPException) as err:
        asyncio.run(admin.add_admin('test-access@example.org', 'admin', [], owner))
    assert err.value.status_code == 400
    assert not recorded
    result = asyncio.run(admin.add_admin('test-access@example.org', 'events', ['access.grant', 'email.send'], owner))
    assert result['ok']
    assert recorded[0][1] == {'permissions': ['access.grant', 'email.send']}
    assert recorded[0][2]['role'] == 'events'


def test_standing_superuser_access_is_immutable(monkeypatch):
    monkeypatch.setattr(admin.settings, 'superuser_email', 'owner@example.org')
    async def fake_user(address, **kwargs):
        return {'id': 1234, 'email': address}
    monkeypatch.setattr(admin, 'resolve_or_create_user', fake_user)
    with pytest.raises(HTTPException) as err:
        asyncio.run(admin.add_admin('owner@example.org', 'superuser', ['email'], make_session('superuser')))
    assert err.value.status_code == 400


def test_admin_listing_exposes_defaults_and_overrides(monkeypatch):
    async def fake_get(path):
        assert path == '/auth/admins'
        return [
            {'user_id': 15, 'email': 'a@example.org', 'role': 'events', 'permissions': None},
            {'user_id': 16, 'email': 'b@example.org', 'role': 'events', 'permissions': ['email']},
        ]
    monkeypatch.setattr(admin.settings, 'superuser_email', '')
    monkeypatch.setattr(admin.database, 'get', fake_get)
    response = asyncio.run(admin.list_admins(make_session('superuser')))
    assert response['admins'][0]['capabilities'] == sorted(roles.capabilities('events'))
    assert response['admins'][1]['capabilities'] == sorted(roles.LEGACY_GRANTS['email'])
    assert len(response['permissions']) == len(roles.PERMISSIONS)
