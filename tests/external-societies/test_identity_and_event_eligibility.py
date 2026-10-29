"""Full external Union identities and explicit event (including external-only) gates."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL','http://127.0.0.1:9')
sys.path.insert(0,str(ROOT/'backend'/'api'))

societies = importlib.import_module('services.societies')
identity = importlib.import_module('services.identity')
admin = importlib.import_module('routes.admin')
events = importlib.import_module('routes.events')
models = importlib.import_module('models.auth')
database = importlib.import_module('clients.database')

run = asyncio.run


def actor(member_now=False, verified=True):
    return SimpleNamespace(user=SimpleNamespace(id=19,email='ab123@ic.ac.uk',email_verified=verified),
                           is_member_now=member_now)


def write_event(**extra):
    row={'slug':'robotics-social','title':'Robotics Social','starts_at':'2026-11-01T18:00:00Z'}
    row.update(extra)
    return row


def test_event_model_explicitly_excludes_eesoc_but_allows_external():
    from importlib.util import spec_from_file_location, module_from_spec
    spec=spec_from_file_location('society_adapter_events',ROOT/'backend'/'database'/'models'/'events.py')
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    EventWrite=module.EventWrite
    exclusive=EventWrite(**write_event(membership_restricted=True,
        allow_eesoc_members=False,eligible_society_ids=[5],
        eesoc_organised=False,organiser_society_ids=[5]))
    assert exclusive.membership_restricted and not exclusive.allow_eesoc_members
    assert exclusive.eligible_society_ids == [5]
    assert exclusive.eesoc_organised is False
    assert exclusive.organiser_society_ids == [5]
    assert EventWrite(**write_event()).membership_restricted is False
    assert EventWrite(**write_event(membership_restricted=True,allow_eesoc_members=True)).eligible_society_ids==[]
    with pytest.raises(ValueError):
        EventWrite(**write_event(membership_restricted=True,allow_eesoc_members=False))
    with pytest.raises(ValueError):
        EventWrite(**write_event(membership_restricted=True,eligible_society_ids=[5,5]))
    with pytest.raises(ValueError):
        EventWrite(**write_event(eesoc_organised=False,organiser_society_ids=[]))


def test_event_access_eesoc_alone_does_not_satisfy_external_only(monkeypatch):
    async def matched(user_id,ids):
        return {5} if user_id==19 and 5 in ids else set()
    monkeypatch.setattr(societies,'eligible_societies',matched)
    exclusive={'membership_restricted':True,'allow_eesoc_members':False,'eligible_society_ids':[5]}
    assert run(events._event_member_allowed(exclusive,actor(member_now=True))) is True
    # EESoc membership by itself is insufficient; no checkbox implicit in the external list.
    monkeypatch.setattr(societies,'eligible_societies',lambda *args: empty())
    assert run(events._event_member_allowed(exclusive,actor(member_now=True))) is False
    assert run(events._event_member_allowed(exclusive,None)) is False
    assert run(events._event_member_allowed(exclusive,actor(verified=False))) is False
    assert run(events._event_member_allowed({**exclusive,'allow_eesoc_members':True},actor(member_now=True))) is True
    assert run(events._event_member_allowed({'membership_restricted':False},None)) is True


async def empty():return set()


def test_public_event_guard_rejects_nonmember_and_guest(monkeypatch):
    exclusive={'membership_restricted':True,'allow_eesoc_members':False,'eligible_society_ids':[5]}
    monkeypatch.setattr(societies,'eligible_societies',lambda *args: empty())
    for user in (None,actor(member_now=True),actor(verified=False)):
        with pytest.raises(HTTPException) as exc:
            run(events._require_event_member(exclusive,user))
        assert exc.value.status_code==403


def test_authorised_event_editor_can_exclude_eesoc_and_unauthorised_cannot(monkeypatch):
    allowed=SimpleNamespace(can=lambda cap: cap=='events.membership_eligibility')
    denied=SimpleNamespace(can=lambda cap: False)
    previous={'membership_restricted':True,'allow_eesoc_members':True,'eligible_society_ids':[5]}
    async def known(path,params=None):
        assert path=='/external-societies'
        return [{'id':5,'name':'Robotics'}]
    monkeypatch.setattr(database,'get',known)
    body={'allow_eesoc_members':False}
    run(admin._check_event_society_eligibility(allowed,previous,body))
    assert body == {'membership_restricted':True,'allow_eesoc_members':False,'eligible_society_ids':[5]}
    with pytest.raises(HTTPException) as exc:
        run(admin._check_event_society_eligibility(denied,previous,{'allow_eesoc_members':False}))
    assert exc.value.status_code==403
    untouched={'title':'Changed title'}
    run(admin._check_event_society_eligibility(denied,previous,untouched))
    assert untouched['allow_eesoc_members'] is True


def test_society_organiser_edit_requires_explicit_grant_and_existing_society(monkeypatch):
    async def directory(path,params=None):
        assert path=='/external-societies'
        return [{'id':5,'name':'Robotics'}]
    monkeypatch.setattr(database,'get',directory)
    editor=SimpleNamespace(can=lambda action: action=='societies.events.assign')
    reader=SimpleNamespace(can=lambda action: False)
    baseline={'eesoc_organised':True,'organiser_society_ids':[]}
    new={'eesoc_organised':False,'organiser_society_ids':[5]}
    run(admin._check_event_organisers(editor,baseline,new))
    assert new['eesoc_organised'] is False
    for payload in ({'eesoc_organised':False,'organiser_society_ids':[5]},
                    {'organiser_society_ids':[5]}):
        with pytest.raises(HTTPException) as exc:
            run(admin._check_event_organisers(reader,baseline,payload))
        assert exc.value.status_code==403
    with pytest.raises(HTTPException) as exc:
        run(admin._check_event_organisers(editor,baseline,
            {'eesoc_organised':False,'organiser_society_ids':[99]}))
    assert exc.value.status_code==400
    with pytest.raises(HTTPException):
        run(admin._check_event_organisers(editor,baseline,
            {'eesoc_organised':False,'organiser_society_ids':[]}))
    legacy={'title':'A renamed event'}
    run(admin._check_event_organisers(reader,{'eesoc_organised':False,'organiser_society_ids':[5]},legacy))
    assert legacy['eesoc_organised'] is False and legacy['organiser_society_ids']==[5]


def test_eligibility_rejects_unknown_society_ids(monkeypatch):
    allowed=SimpleNamespace(can=lambda cap: True)
    async def get(path,params=None):
        assert path=='/external-societies'
        return [{'id':5,'name':'Robotics'}]
    monkeypatch.setattr(database,'get',get)
    with pytest.raises(HTTPException) as exc:
        run(admin._check_event_society_eligibility(allowed,None,
            {'membership_restricted':True,'eligible_society_ids':[999]}))
    assert exc.value.status_code==400


def test_verified_external_user_gets_official_login_name_but_not_eesoc_membership(monkeypatch):
    async def fake_member(*args):return None
    async def external(address):
        assert address=='firstname.lastname@imperial.ac.uk'
        return {'society_id':5,'cid':'12345678','login':'ab123',
                'first_name':'Official','last_name':'Surname',
                'member_type':'Full','order_no':'M123'}
    captured=[]
    async def post(path,payload):
        assert path=='/auth/users/upsert'
        captured.append(payload)
        return payload
    monkeypatch.setattr(identity,'find_member',fake_member)
    monkeypatch.setattr(identity,'find_external_member',external)
    monkeypatch.setattr(database,'post',post)
    res=run(identity.resolve_or_create_user('firstname.lastname@imperial.ac.uk',verified=True))
    assert res['email']=='ab123@ic.ac.uk'
    assert res['first_name']=='Official' and res['last_name']=='Surname'
    assert res['shortcode']=='ab123'
    assert 'member_id' not in res  # External CID is never an EESoc membership.
    assert res['email_verified'] is True


def test_unverified_guest_never_uses_external_profile_to_gain_access(monkeypatch):
    async def fake_member(*args):return None
    async def forbidden(*args):raise AssertionError('Guest must not resolve privileged external identity')
    async def post(path,payload):return payload
    monkeypatch.setattr(identity,'find_member',fake_member)
    monkeypatch.setattr(identity,'find_external_member',forbidden)
    monkeypatch.setattr(database,'post',post)
    result=run(identity.resolve_or_create_user('ab123@ic.ac.uk',verified=False))
    assert result.get('email_verified') is None
    assert result['shortcode']=='ab123'


def test_eligible_society_lookups_are_bound_to_verified_user_id(monkeypatch):
    async def get(path,params=None):
        assert params['user_id']==19 and params['year']
        return {'eligible':path.startswith('/external-societies/5/')}
    monkeypatch.setattr(database,'get',get)
    assert run(societies.eligible_societies(19,[5,7]))=={5}
    assert run(societies.eligible_societies(None,[5]))==set()
