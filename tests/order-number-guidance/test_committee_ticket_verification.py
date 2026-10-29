"""Paid committee bookings must not bypass Union sale verification.

Unit tests are isolated: no real Union calls, emails, or database writes.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:9')
sys.path.insert(0, str(ROOT / 'backend' / 'api'))
from routes import admin  # noqa: E402


class FakeDatabase:
    def __init__(self, status='held'):
        self.status = status
        self.posts = []
        self.patches = []
        self.current = {'id': 42, 'status': status, 'event_id': 11, 'ticket_type_id': 7}

    async def post(self, route, body, **kwargs):
        self.posts.append((route, body))
        if route == '/reservations/committee':
            return self.current
        if route == '/reservations/order-verification/settle':
            return {'matched': 0, 'rejected': 0}
        raise AssertionError(route)

    async def get(self, route, **kwargs):
        assert route == '/reservations/42'
        return self.current

    async def patch(self, route, body, **kwargs):
        self.patches.append((route, body))
        return self.current


def install(monkeypatch, *, needs_purchase=True, status='held'):
    calls = {'created': 0, 'sync': 0, 'confirmation': 0}
    db = FakeDatabase(status)

    async def event(event_id, session):
        return {'id': event_id, 'ticket_types': [
            {'id': 7, 'requires_purchase': needs_purchase}
        ]}

    async def person(email, **kwargs):
        calls['created'] += 1
        return {'id': 30, 'email': email, 'member_id': None}

    async def sync():
        calls['sync'] += 1
        return {'ok': True}

    monkeypatch.setattr(admin, '_bookable_event', event)
    monkeypatch.setattr(admin, 'resolve_or_create_user', person)
    monkeypatch.setattr(admin, 'database', db)
    monkeypatch.setattr(admin.sync_service, 'reconcile_only', sync)
    monkeypatch.setattr(admin.confirmations, 'trigger', lambda: calls.__setitem__(
        'confirmation', calls['confirmation'] + 1))
    return db, calls


def session(*permissions):
    grants = set(permissions)
    return SimpleNamespace(user=SimpleNamespace(id=4, email='admin@example.org'),
                           can=lambda perm: perm in grants)


def add(*, actor, order=None, override=False, capacity=False, send=True):
    return asyncio.run(admin.add_ticket(
        event_id=11, ticket_type_id=7, email='student@example.org',
        first_name='Ada', last_name='Lovelace', quantity=1, note=None,
        order_number=order, override_confirmation=override,
        override_capacity=capacity, send_confirmation=send,
        session=actor,
    ))


def test_paid_ticket_without_order_is_denied_before_creating_user(monkeypatch):
    db, calls = install(monkeypatch)
    with pytest.raises(HTTPException) as error:
        add(actor=session('events.bookings.add'))
    assert error.value.status_code == 400
    assert calls['created'] == 0
    assert not db.posts


def test_unpermissioned_payment_override_is_denied_even_with_order(monkeypatch):
    db, calls = install(monkeypatch)
    with pytest.raises(HTTPException) as error:
        add(actor=session('events.bookings.add'), override=True, order='AB12345')
    assert error.value.status_code == 403
    assert calls['created'] == 0
    assert not db.posts


def test_capacity_override_and_payment_override_are_distinct_grants(monkeypatch):
    db, calls = install(monkeypatch)
    with pytest.raises(HTTPException) as error:
        add(actor=session('events.bookings.add','events.bookings.confirm_override'),
            override=True, capacity=True)
    assert error.value.status_code == 403
    assert calls['created'] == 0
    assert not db.posts


def test_paid_ticket_with_order_stays_held_until_sale_matches(monkeypatch):
    db, calls = install(monkeypatch, status='held')
    result = add(actor=session('events.bookings.add'), order=' AB12345 ')
    assert result['reservation']['status'] == 'held'
    assert db.posts[0][0] == '/reservations/committee'
    assert db.posts[0][1]['order_number'] == 'AB12345'
    assert db.posts[0][1]['override_confirmation'] is False
    assert calls == {'created': 1, 'sync': 1, 'confirmation': 0}
    assert 'Review cancellations' in result['message']


def test_paid_ticket_can_confirm_if_sale_already_matched(monkeypatch):
    _, calls = install(monkeypatch, status='confirmed')
    result = add(actor=session('events.bookings.add'), order='AB12345')
    assert result['reservation']['status'] == 'confirmed'
    assert calls['confirmation'] == 1


def test_authorized_payment_override_confirms_without_order(monkeypatch):
    db, calls = install(monkeypatch, status='confirmed')
    result = add(actor=session('events.bookings.add','events.bookings.confirm_override'),
                 override=True)
    assert result['reservation']['status'] == 'confirmed'
    assert db.posts[0][1]['override_confirmation'] is True
    assert db.posts[0][1]['order_number'] is None
    assert calls['sync'] == 0
    assert calls['confirmation'] == 1


def test_free_ticket_never_requires_a_union_purchase(monkeypatch):
    _, calls = install(monkeypatch, needs_purchase=False, status='confirmed')
    result = add(actor=session('events.bookings.add'))
    assert result['reservation']['status'] == 'confirmed'
    assert calls['confirmation'] == 1


def test_editing_held_order_triggers_existing_sale_matcher(monkeypatch):
    db, calls = install(monkeypatch, status='held')
    async def in_scope(id, actor):
        return db.current
    monkeypatch.setattr(admin, '_reservation_in_scope', in_scope)
    response = asyncio.run(admin.committee_order_number(
        reservation_id=42, order_number='AB76543', session=session('events.bookings.order_number.edit')))
    assert db.patches[0][1]['order_number'] == 'AB76543'
    assert calls['sync'] == 1
    assert 'verification' in response['message'].lower()


def test_existing_order_edits_and_manual_bookings_share_verification_queue():
    src = (ROOT / 'backend/database/routes/events.py').read_text()
    assert src.count('INSERT INTO public.reservation_order_verification') >= 3
    assert "current['order_number'] != value" in src
    assert "pending_payment = bool(tt['requires_purchase']" in src
    assert "if pending_payment and not order_number:" in src
    assert "v.status='rejected'" in src
    assert "v.status!='pending'" in src
    assert "DELETE FROM public.reservation_order_verification" in src


def test_role_reset_fetches_fresh_db_presets_not_old_page_cache():
    admin_view = (ROOT / 'frontend/src/views/AdminView.vue').read_text()
    role_panel = (ROOT / 'frontend/src/components/RoleManagerPanel.vue').read_text()
    assert 'async function refreshAccessPresets()' in admin_view
    assert 'await refreshAccessPresets()' in admin_view
    assert "if (t === 'people') refreshAccessPresets()" in admin_view
    assert '<RoleManagerPanel @updated="refreshAccessPresets" />' in admin_view
    assert "emit('updated')" in role_panel


def test_cancellation_review_can_keep_previously_confirmed_ticket_by_override():
    db = (ROOT / 'backend/database/routes/events.py').read_text()
    view = (ROOT / 'frontend/src/views/AdminView.vue').read_text()
    assert "WHERE id = $1 AND status IN ('held', 'expired', 'confirmed')" in db
    assert "confirmed_at = CASE WHEN status='confirmed' THEN confirmed_at ELSE NOW() END" in db
    assert "WHERE reservation_id=$1 AND status IN ('pending', 'rejected')" in db
    assert "Keep booking (override)" in view
    assert "row.status === 'confirmed' && !override" in view
    assert "can('events.bookings.confirm_override') && Boolean(reviewOverrides[row.id])" in view


def test_same_rejected_claim_may_be_retried_after_new_union_sale():
    source = (ROOT / 'backend/database/routes/events.py').read_text()
    assert "current['status'] not in ('pending', 'matched')" in source
