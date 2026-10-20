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
        'save_announcement', 'submit_announcement',
    },
    'passkeys.py': {
        'sign_in_options', 'sign_in', 'my_passkeys', 'add_passkey_options',
        'add_passkey', 'remove_passkey', 'inspect_link', 'link_options', 'link_register',
    },
    'checkin.py': {'ticket', 'qr_png'},
}
AUDITED_ROUTES = ('admin.py', 'resources.py', 'committee.py', 'approvals.py',
                  'whatsapp.py', 'opportunities.py', 'passkeys.py', 'checkin.py')


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
    assert 'require_any_action("events.view", "events.bookings.view")' in event_listing


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
        'lockers.inventory.view': 'lockers.allowances.grant',
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
            local = ('tab =', 'router.push(', 'saveMyCard', 'resetDraft', 'copyUrl(',
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
