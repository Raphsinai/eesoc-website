"""Audit new EESoc actions, backend route guards, UI buttons and scopes.

Runs without Docker. Adding an administrative action requires registering it,
guarding the API, gating the relevant frontend button, and testing denial.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_permissions_unit import admin, deps, make_session, roles

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / 'backend' / 'api' / 'routes'
VIEWS = ROOT / 'frontend' / 'src'

# Deliberately public or self-service endpoints; do NOT add committee grants.
# The two administrative dashboard endpoints remain general committee-only
# because they are cross-domain, and their private data are filtered by grant.
PUBLIC_AND_SELF_SERVICE = {
    'admin.py': {'dashboard', 'overview'},
    'resources.py': {'list_pages', 'get_page'},
    'committee.py': {'get_committee', 'my_card', 'update_my_card', 'upload_my_photo'},
    'opportunities.py': {
        'published_opportunities', 'published_opportunity', 'my_opportunities',
        'submit_opportunity', 'upload_opportunity_image', 'resubmit_opportunity',
        'save_announcement', 'submit_announcement', 'withdraw_my_opportunity', 'submit_my_draft',
    },
    'passkeys.py': {
        'sign_in_options', 'sign_in', 'my_passkeys', 'add_passkey_options',
        'add_passkey', 'remove_passkey', 'inspect_link', 'link_options', 'link_register',
    },
    'checkin.py': {'ticket', 'qr_png'},
    'photos.py': {'public_photo_set', 'public_print', 'download_photos'},
}
AUDITED_ROUTES = ('admin.py', 'resources.py', 'committee.py', 'approvals.py',
                  'whatsapp.py', 'opportunities.py', 'passkeys.py', 'checkin.py', 'photos.py')


def _route_decorators(filename):
    source = (ROUTES / filename).read_text()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not isinstance(dec.func.value, ast.Name):
                continue
            if dec.func.value.id not in ('router', 'admin_router'):
                continue
            if dec.func.attr not in ('get', 'post', 'put', 'patch', 'delete'):
                continue
            yield node.name, dec.func.attr, ast.get_source_segment(source, dec)


def test_every_admin_route_has_named_action_guard_or_documented_self_service_exception():
    unprotected = []
    registered = set(roles.PERMISSIONS)
    for file in AUDITED_ROUTES:
        exception = PUBLIC_AND_SELF_SERVICE.get(file, set())
        for function, method, decorator in _route_decorators(file):
            if function in exception:
                continue
            names = re.findall(r'require_(?:any_)?action\([\s\S]*?\)', decorator)
            if not names:
                unprotected.append(f'{file}:{function} {method.upper()} (no action guard)')
                continue
            for name in re.findall(r'"([a-z_.]+)"', ' '.join(names)):
                if name not in registered:
                    unprotected.append(f'{file}:{function} unknown permission {name}')
    assert not unprotected, '\n'.join(unprotected)


def test_booking_view_can_open_calendar_without_event_edit_permission():
    decorators = list(_route_decorators('admin.py'))
    event_listing = next(code for name, _, code in decorators if name == 'list_all_events')
    assert 'require_any_action("events.view", "events.bookings.view", "events.create")' in event_listing


def test_action_catalogue_has_unique_keys_and_no_wildcards_or_group_grants():
    catalogue = roles.describe_permissions()
    assert len(catalogue) >= 125
    assert len({p['id'] for p in catalogue}) == len(catalogue)
    assert all('.' in p['id'] and '*' not in p['id'] for p in catalogue)
    assert all(p['group'] and p['subgroup'] and p['label'] for p in catalogue)
    assert not set(roles.LEGACY_GRANTS) & set(roles.PERMISSIONS)
    assert roles.validate_permissions(['events.bookings.confirm']) == ['events.bookings.confirm']
    for invalid in ('events', 'bookings', 'site', 'events.*', 'events.bookings', '*'):
        with pytest.raises(ValueError):
            roles.validate_permissions([invalid])


def test_category_is_only_ui_bulk_select_not_a_server_permission():
    assert roles.effective_capabilities('events', []) == frozenset()
    assert roles.effective_capabilities('industry', ['events.bookings.confirm']) == {'events.bookings.confirm'}
    permissions_file = (VIEWS / 'components' / 'PermissionSelector.vue').read_text()
    for marker in ('Select all', 'Clear all', 'setMany(group.items', 'setMany(subgroup.items',
                   'setMany([permission]', ':indeterminate.prop', 'search', 'permission.id'):
        assert marker in permissions_file


def test_old_saved_grants_expand_but_cannot_be_saved_again():
    old = roles.effective_capabilities('events', ['events', 'bookings', 'industry_bookings'])
    assert {'events.create', 'events.edit', 'events.bookings.confirm',
            'events.bookings.cancel', 'events.scope.all', 'events.scope.industry'} <= old
    assert 'sponsors.delete' not in old
    with pytest.raises(ValueError):
        roles.validate_permissions(['events', 'bookings'])


def test_individually_revocable_event_actions_and_different_scopes():
    grants = ['events.bookings.confirm', 'events.scope.industry']
    user = make_session('admin', grants)
    guard = deps.require_action('events.bookings.confirm')
    assert asyncio.run(guard(user)) is user
    for denied in ('events.bookings.cancel', 'events.bookings.add', 'events.checkin.manual',
                   'events.checkin.scan', 'events.publish', 'events.create',
                   'events.ticket_types.edit', 'events.ticket_types.create'):
        with pytest.raises(HTTPException) as error:
            asyncio.run(deps.require_action(denied)(user))
        assert error.value.status_code == 403
    deps.assert_event_scope(user, {'industry': True})
    with pytest.raises(HTTPException) as error:
        deps.assert_event_scope(user, {'industry': False})
    assert error.value.status_code == 403


def test_event_ticket_form_does_not_allow_create_edit_delete_from_event_edit_alone():
    prev = {'ticket_types': [{
        'id': 19, 'name': 'Standard', 'description': '', 'price_pence': 500,
        'requires_purchase': True,
    }]}
    only_edit = make_session('events', ['events.edit', 'events.scope.all'])
    # A partial non-ticket update must never require tickets edit privileges.
    admin._assert_ticket_type_changes(only_edit, prev, {'title': 'Updated schedule'})
    admin._assert_ticket_type_changes(only_edit, prev, {'ticket_types': [prev['ticket_types'][0]]})
    for payload, needed in (
        ({'ticket_types': [prev['ticket_types'][0], {'name': 'VIP', 'price_pence': 900}]}, 'events.ticket_types.create'),
        ({'ticket_types': [{**prev['ticket_types'][0], 'price_pence': 100}]}, 'events.ticket_types.edit'),
        ({'ticket_types': []}, 'events.ticket_types.delete'),
    ):
        with pytest.raises(HTTPException) as error:
            admin._assert_ticket_type_changes(only_edit, prev, payload)
        assert error.value.status_code == 403
        assert needed in error.value.detail
    assert not only_edit.can('events.publish')
    ticket_editor = make_session('events', ['events.edit', 'events.ticket_types.edit', 'events.scope.all'])
    admin._assert_ticket_type_changes(ticket_editor, prev, {'ticket_types': [{**prev['ticket_types'][0], 'price_pence': 100}]})


def test_one_permission_does_not_grant_all_other_operations_in_its_category():
    sample = {
        'files.upload': 'files.delete',
        'access.view': 'access.impersonate',
        'whatsapp.view': 'whatsapp.post',
        'email.queue.view': 'email.queue.send',
        'sponsors.view': 'sponsors.delete',
        'lockers.inventory.view': 'lockers.bookings.release',
        'lockers.bookings.view': 'lockers.allowances.grant',
        'opportunities.view': 'opportunities.approve',
        'sponsors.portal.view': 'sponsors.portal.jobs.create',
    }
    for allowed, denied in sample.items():
        session = make_session('admin', [allowed])
        assert session.can(allowed) and not session.can(denied)


def test_private_action_buttons_in_core_views_are_gated():
    """New buttons need an exact permission (or a documented UI exception)."""
    for filename in ('AdminView.vue', 'AdminEventView.vue', 'AdminSponsorView.vue',
                     'CheckinView.vue', 'OpportunitiesAdminPanel.vue'):
        prefix = 'views' if filename != 'OpportunitiesAdminPanel.vue' else 'components'
        text = (VIEWS / prefix / filename).read_text()
        for match in re.finditer(r'<Button\b[^>]*>', text, re.S):
            tag = match.group(0)
            click = re.search(r'@click="([^"]+)', tag)
            if not click:
                continue
            action = click.group(1)
            # Pure navigation/local dismiss, reset, or own-card edit do not
            # require an administrative grant; the destination may have one.
            # changeEventPage only slices the already permission-scoped event
            # list locally; it neither requests additional data nor mutates it.
            local = ('tab =', 'router.push(', 'changeEventPage(',
                     'saveMyCard', 'resetDraft', 'copyUrl(',
                     'resetEditPermissions', 'resetNewPermissions', 'resetSponsor',
                     'closeCompose', 'stopScan', 'dismissResult', 'adding = false',
                     'refresh', 'showAll =', "emit('open-email'")
            if any(action.startswith(v) for v in local):
                continue
            assert 'can(' in tag or any(flag in tag for flag in ('e.can_publish', 'e.can_delete', 'e.can_edit', 'e.can_book')), f'{filename} button {action} is missing a per-action UI gate'


def test_future_agents_have_a_mandatory_contract():
    instructions = (ROOT / 'AGENTS.md').read_text()
    claude = (ROOT / 'CLAUDE.md').read_text()
    for phrase in ('one stable permission', 'backend/api/utils/roles.py', 'require_action',
                   'frontend/src/stores/session.js', 'Category', '403', 'production'):
        assert phrase.lower() in instructions.lower()
    assert 'AGENTS.md' in claude


def test_self_service_reservation_never_accepts_another_user_even_with_bookings_view(monkeypatch):
    import importlib
    events = importlib.import_module('routes.events')

    async def fake_get(path, params=None):
        if path == '/reservations/72':
            return {'id': 72, 'event_id': 300, 'user_id': 999}
        pytest.fail(f'Unexpected database request: {path}')

    monkeypatch.setattr(events.database, 'get', fake_get)
    session = make_session('admin', ['events.bookings.view', 'events.scope.all'])
    with pytest.raises(HTTPException) as denied:
        asyncio.run(events._own_reservation(72, session))
    assert denied.value.status_code == 404


def test_public_locker_release_requires_ownership_or_specific_release_permission(monkeypatch):
    import importlib
    lockers = importlib.import_module('routes.lockers')
    released = []

    async def fake_get(path, params=None):
        assert path == '/lockers/bookings'
        return []

    async def fake_post(path, json=None, params=None):
        released.append(path)
        return {'ok': True}

    monkeypatch.setattr(lockers.database, 'get', fake_get)
    monkeypatch.setattr(lockers.database, 'post', fake_post)
    viewer = make_session('admin', ['lockers.inventory.view'])
    with pytest.raises(HTTPException) as denied:
        asyncio.run(lockers.release(54, viewer))
    assert denied.value.status_code == 404
    assert not released
    operator = make_session('admin', ['lockers.bookings.release'])
    assert asyncio.run(lockers.release(54, operator))['ok']
    assert released == ['/lockers/bookings/54/release']


def test_published_only_events_for_a_create_only_operator(monkeypatch):
    import importlib
    events = importlib.import_module('routes.events')
    params_seen = []

    async def fake_get(path, params=None):
        assert path == '/events'
        params_seen.append(params)
        # Fail closed even if an older DB adapter ignores published_only.
        return [{'published': False, 'industry': False}]

    monkeypatch.setattr(events.database, 'get', fake_get)
    creator = make_session('admin', ['events.create', 'events.scope.all'])
    result = asyncio.run(events.list_events(session=creator))
    assert params_seen[0]['published_only'] is True
    assert result['events'] == []


def test_sponsor_inbox_can_be_read_without_email_send_and_is_still_permission_protected(monkeypatch):
    """Regression: a read-only sponsor inbox was throwing a NameError/HTTP 500.

    A separate email.send grant is only required when actually sending mail.
    """
    sponsor_reader = make_session('admin', ['sponsors.inbox'])
    assert asyncio.run(deps.require_action('sponsors.inbox')(sponsor_reader)) is sponsor_reader
    assert asyncio.run(deps.require_sponsors(sponsor_reader)) is sponsor_reader
    assert not sponsor_reader.can('email.send')
    unauthorized = make_session('admin', ['sponsors.view'])
    with pytest.raises(HTTPException) as err:
        asyncio.run(deps.require_action('sponsors.inbox')(unauthorized))
    assert err.value.status_code == 403

    called = []
    async def fake_get(path, params=None):
        called.append(path)
        if path == '/sponsors/listings/unannounced':
            return [{'id': 5, 'title': 'Summer placement',
                     'sponsor_name': 'Acme', 'location': 'London'}]
        return []

    monkeypatch.setattr(admin.database, 'get', fake_get)
    response = asyncio.run(admin.sponsor_inbox(sponsor_reader))
    assert response['listings'] == [
        {'id': 5, 'title': 'Summer placement', 'sponsor': 'Acme', 'location': 'London'},
    ]
    assert response['notes'] == response['marketing'] == response['requests'] == response['interest'] == []
    assert '/sponsors/listings/unannounced' in called


def test_admin_sponsor_loader_uses_each_read_permission_instead_of_directory_permission():
    frontend = (VIEWS / 'views' / 'AdminView.vue').read_text()
    loader = frontend.split('async function loadSponsorInbox()', 1)[1].split('\n}\n', 1)[0]
    assert "can('sponsors.inbox')" in loader
    assert "can('sponsors.digest.view')" in loader
    assert "can('sponsors.view')" not in loader


def test_committee_order_number_requires_its_own_write_permission():
    decorators = list(_route_decorators('admin.py'))
    method, guard = next((method, code) for name, method, code in decorators
                         if name == 'committee_order_number')
    assert method == 'patch'
    assert 'require_action("events.bookings.order_number.edit")' in guard
    assert 'verify_csrf' in guard
    source = (VIEWS / 'views' / 'AdminEventView.vue').read_text()
    assert "can('events.bookings.order_number.edit')" in source
    assert '/admin/reservations/${row.id}/order-number' in source


def test_capacity_override_is_independent_of_adding_tickets():
    assert 'events.bookings.capacity_override' in roles.PERMISSIONS
    assert 'events.bookings.capacity_override' in roles.capabilities('superuser')
    for name in ('admin', 'events', 'industry'):
        assert 'events.bookings.capacity_override' not in roles.capabilities(name)
    assert 'events.bookings.capacity_override' not in roles.LEGACY_GRANTS['bookings']
    assert 'events.bookings.capacity_override' not in roles.LEGACY_GRANTS['industry_bookings']
    source = (ROUTES / 'admin.py').read_text()
    assert 'if override_capacity and not session.can(\'events.bookings.capacity_override\'):' in source
    front = (VIEWS / 'views' / 'AdminEventView.vue').read_text()
    assert "can('events.bookings.capacity_override')" in front
    assert 'ticket.override_capacity' in front


def test_capacity_override_denied_before_member_creation(monkeypatch):
    async def bookable(*args):
        return {'id': 8}
    async def no_create(*args, **kwargs):
        pytest.fail('Must deny override before creating a member')
    monkeypatch.setattr(admin, '_bookable_event', bookable)
    monkeypatch.setattr(admin, 'resolve_or_create_user', no_create)
    limited = make_session('events', ['events.bookings.add', 'events.scope.all'])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.add_ticket(
            8, 2, 'member@example.org', None, None, 1, None, False, True, limited,
        ))
    assert exc.value.status_code == 403


def test_activity_log_is_separately_revocable_and_private():
    assert 'access.audit.view' in roles.PERMISSIONS
    assert 'access.audit.view' in roles.capabilities('superuser')
    assert 'access.audit.view' not in roles.capabilities('admin')
    assert 'access.audit.view' not in roles.LEGACY_GRANTS['users']
    guard = next(code for name, method, code in _route_decorators('admin.py')
                 if name == 'audit_actions' and method == 'get')
    assert 'require_action(\'access.audit.view\')' in guard
    view = (VIEWS / 'views' / 'AdminView.vue').read_text()
    assert "['audit', 'Activity log', 'access.audit.view']" in view
    assert "can('access.audit.view')" in view


def test_mutation_journal_does_not_store_raw_http_bodies_or_cookies():
    api_main = (ROOT / 'backend' / 'api' / 'main.py').read_text()
    db_main = (ROOT / 'backend' / 'database' / 'main.py').read_text()
    assert 'database.audit_context(actor_id, acting_as_id, request_id)' in api_main
    assert "await database.post('/audit/actions', entry)" in api_main
    assert 'actor_id' in db_main and 'acting_as_id' in db_main
    assert 'request.body()' not in api_main
    assert 'request.body()' not in db_main
    assert 'dict(request.headers)' not in api_main
    assert 'dict(request.headers)' not in db_main
