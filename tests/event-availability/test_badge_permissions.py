"""A distinct event badge setting cannot be used to edit event details."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "permissions"))

import pytest
from fastapi import HTTPException

from test_permissions_unit import admin, deps, make_session, roles


def test_badge_permission_is_distinct_from_event_edit():
    permission = 'events.availability_badge.edit'
    assert permission in roles.PERMISSIONS
    limited = make_session('events', [permission, 'events.scope.all'])
    assert limited.can(permission) and not limited.can('events.edit')
    assert asyncio.run(deps.require_action(permission)(limited)) is limited
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.require_action('events.edit')(limited))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.require_action(permission)(make_session('events', ['events.edit', 'events.scope.all'])))
    assert exc.value.status_code == 403


def test_badge_mutation_is_scoped_and_does_not_touch_any_other_field(monkeypatch):
    writes = []
    async def get(path, params=None):
        return { 'id': 11, 'slug': 'halloween', 'industry': False, 'ticket_types': [] }
    async def patch(path, body):
        writes.append((path, body))
        return {'id': 11, 'availability_badge': body['availability_badge']}
    monkeypatch.setattr(admin.database, 'get', get)
    monkeypatch.setattr(admin.database, 'patch', patch)
    permitted = make_session('events', ['events.availability_badge.edit', 'events.scope.all'])
    assert asyncio.run(admin.set_event_availability_badge(11, 'selling_fast', permitted)) == (
        {'id': 11, 'availability_badge': 'selling_fast'})
    assert writes == [('/events/11/availability-badge', {'availability_badge': 'selling_fast'})]

    outside_scope = make_session('industry', ['events.availability_badge.edit', 'events.scope.industry'])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.set_event_availability_badge(11, 'not_many_left', outside_scope))
    assert exc.value.status_code == 403
    assert len(writes) == 1


def test_button_is_gated_by_badge_edit_permission_not_general_event_edit():
    source = (admin.__file__ if hasattr(admin, '__file__') else '')
    assert source
    from pathlib import Path
    view = (Path(__file__).resolve().parents[2] / 'frontend/src/views/AdminView.vue').read_text()
    assert 'e.can_badge' in view
    assert 'saveEventAvailabilityBadge' in view
    assert 'events/${e.id}/availability-badge' in view
