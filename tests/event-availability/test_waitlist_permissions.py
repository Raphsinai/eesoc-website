"""Fine-grained waitlist setting, independent of ordinary event editing."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'permissions'))
from test_permissions_unit import admin, deps, make_session, roles


def test_waitlist_permission_is_individually_grantable_and_revocable():
    permission = 'events.waitlist.manage'
    assert permission in roles.PERMISSIONS
    admin_only_waitlist = make_session('events', [permission, 'events.scope.all'])
    assert asyncio.run(deps.require_action(permission)(admin_only_waitlist)) is admin_only_waitlist
    assert not admin_only_waitlist.can('events.edit')
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.require_action(permission)(make_session('events', ['events.edit', 'events.scope.all'])))
    assert exc.value.status_code == 403


def test_toggle_is_scoped_and_changes_only_the_waitlist_bit(monkeypatch):
    patches = []
    async def read(path, params=None):
        assert path == '/events/17'
        return {'id': 17, 'industry': False, 'waitlist_enabled': True}
    async def patch(path, body):
        patches.append((path, body))
        return {'id': 17, 'waitlist_enabled': body['waitlist_enabled']}
    monkeypatch.setattr(admin.database, 'get', read)
    monkeypatch.setattr(admin.database, 'patch', patch)

    permitted = make_session('events', ['events.waitlist.manage', 'events.scope.all'])
    assert asyncio.run(admin.set_event_waitlist_enabled(17, False, permitted)) == {
        'id': 17, 'waitlist_enabled': False,
    }
    assert patches == [('/events/17/waitlist-enabled', {'waitlist_enabled': False})]
    industry_only = make_session('industry', ['events.waitlist.manage', 'events.scope.industry'])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.set_event_waitlist_enabled(17, False, industry_only))
    assert exc.value.status_code == 403
    assert len(patches) == 1
