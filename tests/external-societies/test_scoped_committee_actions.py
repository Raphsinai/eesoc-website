"""Society committee actions have EESoc parity but cannot cross a tenant boundary."""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL','http://127.0.0.1:9')
sys.path.insert(0,str(ROOT/'backend'/'api'))
from models.auth import AuthSession, User, Session
societies=importlib.import_module('services.societies')
actions=importlib.import_module('routes.society_event_actions')
portal=importlib.import_module('routes.societies')
database=importlib.import_module('clients.database')
role_table=importlib.import_module('utils.roles')
run=asyncio.run


def user():
    return AuthSession(session=Session(token_hash='test',csrf_token='csrf',user_id=7,
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1)),
        user=User(id=7,email='abc123@ic.ac.uk',email_verified=True))


@pytest.fixture
def scope(monkeypatch):
    membership={'id':3,'name':'Robotics','managing':True,
        'permissions':['events.view','events.bookings.view','events.bookings.add',
            'events.bookings.confirm','events.bookings.remind','events.bookings.import.view',
            'events.bookings.capacity_override','events.checkin.scan',
            'events.checkin.manual','events.checkin.view','access.grant',
            'access.view','access.roles.create','email.announce_event']}
    async def affiliations(session): return [membership]
    async def get(path,params=None):
        if path == '/external-societies/3/roles':return []
        if path == '/external-societies/3/committee':return []
        if path.endswith('/assigned'):
            # only event 11 is assigned, event 12 is someone else's
            return {'assigned':'/events/11/' in path}
        if path.startswith('/events/'):
            return {'id':int(path.split('/')[2]),'title':'Robotics'}
        if path.startswith('/reservations/'):
            return {'id':int(path.split('/')[2]),'event_id':11 if int(path.split('/')[2])==51 else 12}
        if path.startswith('/product-sales/rows/'):
            return {'id':int(path.split('/')[3]),'event_id':11 if int(path.split('/')[3])==91 else 12}
        raise AssertionError('Unexpected adapter path: '+path)
    monkeypatch.setattr(societies,'affiliations',affiliations)
    monkeypatch.setattr(database,'get',get)
    return membership


def test_society_actions_share_granular_event_permissions():
    eesoc={p for p in role_table.PERMISSIONS if p.startswith('events.')}
    global_scope={'events.scope.all','events.scope.industry',
                  'events.sync.union','events.sync.reconcile'}
    assert eesoc-global_scope <= societies.SOC_ACTIONS
    assert not global_scope & societies.SOC_ACTIONS
    for key in ('events.bookings.view','events.bookings.export',
                'events.bookings.add','events.bookings.confirm',
                'events.bookings.capacity_override','events.bookings.confirm_override',
                'events.checkin.scan','events.ticket_types.edit',
                'email.announce_event','access.grant','access.roles.edit'):
        assert key in societies.SOC_ACTIONS, key


def test_scoped_session_is_user_audited_and_cannot_grant_global_tools(scope):
    proxy=societies.ScopedCommitteeSession(user(),scope)
    assert proxy.user.id==7
    assert proxy.can('events.bookings.add')
    assert proxy.can('events.bookings.capacity_override')
    assert not proxy.can('events.bookings.confirm_override')
    assert not proxy.can('access.impersonate')
    assert not proxy.can('events.sync.union')
    assert proxy.can('events.scope.all')  # Only after explicit assigned-event guard.
    assert societies.grants(['events.tickets.edit','announcements.send']) >= {
        'events.ticket_types.edit','email.send','email.announce_event'}


def test_booking_detail_returns_admin_parity_and_denies_other_events(monkeypatch,scope):
    calls=[]
    async def detail(event_id, session):
        assert session.can('events.bookings.view')
        calls.append((event_id,session.user.id))
        return {'reservations':[{'id':51,'email':'member@ic.ac.uk'}],
                'waitlist':[],'summary':[]}
    monkeypatch.setattr(actions.eesoc,'event_detail',detail)
    result=run(actions.booking_detail(3,11,user()))
    assert result['reservations'][0]['id']==51
    assert calls==[(11,7)]
    with pytest.raises(HTTPException) as exc:
        run(actions.booking_detail(3,12,user()))
    assert exc.value.status_code==403
    assert calls==[(11,7)]


def test_manual_ticket_and_override_receive_exact_event_and_real_audit_user(monkeypatch,scope):
    seen=[]
    async def create(**kw):
        seen.append(kw)
        assert kw['session'].user.id==7
        assert kw['session'].can('events.bookings.add')
        assert kw['session'].can('events.bookings.capacity_override')
        return {'ok':True}
    monkeypatch.setattr(actions.eesoc,'add_ticket',create)
    ticket=actions.ManualBooking(ticket_type_id=4,email='member@ic.ac.uk',override_capacity=True)
    assert run(actions.add_booking(3,11,ticket,user()))['ok']
    assert seen[0]['event_id']==11 and seen[0]['override_capacity'] is True
    with pytest.raises(HTTPException):
        run(actions.add_booking(3,12,ticket,user()))
    assert len(seen)==1


def test_foreign_reservation_id_never_reaches_underlying_booking_operations(monkeypatch,scope):
    async def never(**kwargs):raise AssertionError('Foreign reservation reached action')
    monkeypatch.setattr(actions.eesoc,'remind',never)
    monkeypatch.setattr(actions.eesoc,'confirm_manually',never)
    monkeypatch.setattr(actions.eesoc,'cancel_reservation',never)
    for call in (
        lambda: actions.remind_booking(3,11,52,user()),
        lambda: actions.confirm_booking(3,11,52,actions.Confirm(order_number='123'),user()),
    ):
        with pytest.raises(HTTPException) as err:run(call())
        assert err.value.status_code==404


def test_imported_sale_and_checkin_cannot_cross_event_scope(monkeypatch,scope):
    async def never(*args,**kwargs):raise AssertionError('Foreign data reached action')
    monkeypatch.setattr(actions.product_sales,'search_import_reservations',never)
    with pytest.raises(HTTPException) as exc:
        run(actions.sale_candidates(3,11,92,session=user()))
    assert exc.value.status_code==404
    monkeypatch.setattr(actions.tickets,'parse',lambda _:999)
    monkeypatch.setattr(actions.checkin,'_booking',lambda _:returns({'event_id':12}))
    monkeypatch.setattr(actions.checkin,'scan',never)
    with pytest.raises(HTTPException) as exc:
        run(actions.checkin_scan(3,11,'abc',user()))
    assert exc.value.status_code==403


async def returns(value):return value


def test_society_access_cannot_grant_more_than_manager_owns(monkeypatch,scope):
    async def forbid(*args,**kwargs):raise AssertionError('Must not write elevated permission')
    monkeypatch.setattr(database,'put',forbid)
    with pytest.raises(HTTPException) as err:
        run(portal.grant_my_committee(3,'ef456',portal.CommitteeWrite(
            permissions=['events.bookings.confirm_override']),user()))
    assert err.value.status_code==403
    with pytest.raises(HTTPException) as err:
        run(portal.grant_my_committee(4,'ef456',portal.CommitteeWrite(
            permissions=['events.bookings.add']),user()))
    assert err.value.status_code==403
    with pytest.raises(HTTPException) as err:
        run(portal.grant_my_committee(3,'ef456',portal.CommitteeWrite(
            role_id='high-privilege',permissions=None),user()))
    assert err.value.status_code in (400,403)


def test_role_creation_grants_are_subset_of_managers_rights(monkeypatch,scope):
    async def get(path,params=None):
        if path=='/external-societies/3/roles':return []
        raise AssertionError(path)
    async def put(path,value):
        assert path=='/external-societies/3/roles/events'
        return {'ok':True}
    monkeypatch.setattr(database,'get',get)
    monkeypatch.setattr(database,'put',put)
    body=portal.RoleWrite(title='Events team',permissions=['events.bookings.add'])
    assert run(portal.save_my_role(3,'events',body,user()))['ok']
    with pytest.raises(HTTPException) as err:
        run(portal.save_my_role(3,'admin',portal.RoleWrite(
            title='Site admins',permissions=['events.bookings.confirm_override']),user()))
    assert err.value.status_code==403


def test_read_only_society_member_can_view_bookings_but_not_add_tickets(monkeypatch,scope):
    scope['managing']=False
    async def detail(event_id,session):
        return {'reservations':[{'id':51,'event_id':11}],'summary':[],'waitlist':[]}
    monkeypatch.setattr(actions.eesoc,'event_detail',detail)
    assert run(actions.booking_detail(3,11,user()))['reservations'][0]['id']==51
    with pytest.raises(HTTPException) as exc:
        run(actions.add_booking(3,11,actions.ManualBooking(
            ticket_type_id=3,email='test@ic.ac.uk'),user()))
    assert exc.value.status_code==403


def test_society_product_link_never_mutates_another_events_ticket(monkeypatch,scope):
    scope['permissions'].append('events.ticket_types.product')
    async def get(path,params=None):
        if path.endswith('/assigned'):return {'assigned':'/events/11/' in path}
        if path.startswith('/events/'):return {'id':int(path.split('/')[2])}
        if path.startswith('/ticket-types/'):
            return {'id':int(path.split('/')[2]),'event_id':12}
        raise AssertionError(path)
    async def should_not_link(**kwargs):
        raise AssertionError('A foreign ticket reached product mutation')
    monkeypatch.setattr(database,'get',get)
    monkeypatch.setattr(actions.eesoc,'set_ticket_product',should_not_link)
    with pytest.raises(HTTPException) as exc:
        run(actions.link_ticket_product(3,11,91,shop_product_ref='3023',session=user()))
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        run(actions.link_event_product(3,12,shop_product_ref='3023',session=user()))
    assert exc.value.status_code==403
