"""Society tenants are never EESoc admins and cannot cross audience/group boundaries."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL','http://127.0.0.1:9')
sys.path.insert(0,str(ROOT/'backend'/'api'))
from fastapi import HTTPException

societies = importlib.import_module('services.societies')
emailing = importlib.import_module('services.emailing')
whatsapp = importlib.import_module('services.whatsapp')
portal = importlib.import_module('routes.societies')
database = importlib.import_module('clients.database')
link = importlib.import_module('clients.whatsapp')

run=asyncio.run


def session(email='abc123@ic.ac.uk',verified=True):
    return SimpleNamespace(user=SimpleNamespace(id=7,email=email,email_verified=verified))


def test_college_login_requires_verified_session():
    assert societies.identity_login(session())=='abc123'
    for mail,verified in [('abc123@example.com',True),('abc123@ic.ac.uk',False),('invalid+tag@ic.ac.uk',True)]:
        with pytest.raises(HTTPException) as exc:
            societies.identity_login(session(mail,verified))
        assert exc.value.status_code==403


def test_external_committee_must_be_explicitly_named_and_have_exact_permission(monkeypatch):
    async def get(path,params=None):
        if path=='/external-societies/for-login/abc123':
            return [{'id':3,'name':'Robotics','managing':True,'permissions':['events.view','events.edit']}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    assert run(societies.require_society(session(),3,'events.edit',managing=True))['id']==3
    for id_,action in [(4,'events.edit'),(3,'whatsapp.post'),(3,'events.publish')]:
        with pytest.raises(HTTPException) as exc:
            run(societies.require_society(session(),id_,action,managing=True))
        assert exc.value.status_code==403


def test_nonmanaging_committee_cannot_manage_even_if_action_granted(monkeypatch):
    async def get(path,params=None):
        return [{'id':3,'name':'Robotics','managing':False,'permissions':['events.edit']}]
    monkeypatch.setattr(database,'get',get)
    with pytest.raises(HTTPException) as exc:
        run(societies.require_society(session(),3,'events.edit',managing=True))
    assert exc.value.status_code==403


def test_assigned_event_required_and_scope_cannot_cross(monkeypatch):
    async def get(path,params=None):
        if path.endswith('/events/11/assigned'):return {'assigned':True}
        if path.endswith('/events/12/assigned'):return {'assigned':False}
        if path=='/events/11':return {'id':11,'title':'Robotics'}
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    assert run(societies.require_event(3,11))['id']==11
    with pytest.raises(HTTPException) as exc:
        run(societies.require_event(3,12))
    assert exc.value.status_code==403


def test_mailing_list_needs_explicit_society_grant(monkeypatch):
    async def one(key):
        assert key=='society:3'
        return [{'email':'abc123@ic.ac.uk'}, {'email':'ABC123@ic.ac.uk'}]
    monkeypatch.setattr(emailing,'_resolve_one',one)
    with pytest.raises(HTTPException) as exc:
        run(emailing.resolve(['society:3']))
    assert exc.value.status_code==403
    with pytest.raises(HTTPException):
        run(emailing.resolve(['society:4'],allowed_society_ids={3}))
    assert run(emailing.resolve(['society:3'],allowed_society_ids={3}))==[{'email':'abc123@ic.ac.uk'}]


def test_external_membership_matches_login_for_only_requested_societies(monkeypatch):
    async def get(path,params=None):
        assert path.endswith('/ticket-eligible')
        return {'eligible':path.startswith('/external-societies/3/')}
    monkeypatch.setattr(database,'get',get)
    assert run(societies.eligible_societies('abc123@ic.ac.uk',[3,4]))=={3}
    assert run(societies.eligible_societies('abc123@gmail.com',[3]))==set()


def test_disabled_and_unassigned_whatsapp_groups_cannot_be_selected(monkeypatch):
    rows=[
        {'account_key':'eesoc','jid':'111@g.us','enabled':True,'eesoc_enabled':True,
         'eesoc_user_ids':[7],'society_id':3},
        {'account_key':'eesoc','jid':'222@g.us','enabled':False,'eesoc_enabled':True,
         'eesoc_user_ids':[7],'society_id':3},
        {'account_key':'eesoc','jid':'333@g.us','enabled':True,'eesoc_enabled':False,
         'eesoc_user_ids':[7],'society_id':4},
    ]
    async def get(path,params=None):
        assert path=='/external-societies/whatsapp/groups/settings'
        return rows
    async def groups(account='eesoc'):
        return [{'jid':row['jid'],'name':row['jid'],'canPost':True,'size':100}
                for row in rows if row['account_key']==account]
    monkeypatch.setattr(database,'get',get)
    monkeypatch.setattr(link,'groups',groups)
    monkeypatch.setattr(link,'configured',lambda:True)
    assert [g['jid'] for g in run(whatsapp.available(user_id=7))]==['111@g.us']
    assert run(whatsapp.available(user_id=8))==[]
    assert [g['jid'] for g in run(whatsapp.available(society_id=3))]==['111@g.us']
    assert [g['jid'] for g in run(whatsapp.available(society_id=4))]==['333@g.us']
    assert run(whatsapp.pick(['222@g.us'],society_id=3))==[]
    # Even group-config administrators never acquire posting rights by accident.
    assert run(whatsapp.available(user_id=8,admin_all=True))==[]


def test_manager_cannot_use_another_society_audience_or_disabled_group(monkeypatch):
    async def require(*args,**kwargs):return {'id':3,'permissions':['announcements.send']}
    async def pick(keys,**kwargs):return []
    monkeypatch.setattr(societies,'require_society',require)
    monkeypatch.setattr(whatsapp,'pick',pick)
    with pytest.raises(HTTPException) as exc:
        run(portal._check_audiences(3,['society:4'],session()))
    assert exc.value.status_code==403
    with pytest.raises(HTTPException) as exc:
        run(portal._check_audiences(3,['whatsapp:unassigned@g.us'],session()))
    assert exc.value.status_code==403
