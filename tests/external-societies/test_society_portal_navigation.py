"""A named society committee can find its portal with verified Union aliases."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL','http://127.0.0.1:9')
sys.path.insert(0,str(ROOT/'backend'/'api'))
from models.auth import AuthSession, User, Session
societies = importlib.import_module('services.societies')
database = importlib.import_module('clients.database')
run = asyncio.run


def session(address, verified=True, member=None, impersonating=False):
    return SimpleNamespace(
        user=SimpleNamespace(id=27,email=address,email_verified=verified,member_id=getattr(member,'id',None)),
        member=member,
        is_impersonating=impersonating,
    )


def real_impersonation(address):
    """Impersonation is authenticated by a server-hydrated originator, not UI state."""
    return AuthSession(
        session=Session(token_hash='test',csrf_token='csrf',user_id=27,
                        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
                        impersonator_id=1),
        user=User(id=27,email=address,email_verified=False),
        impersonator=User(id=1,email='authorized-admin@ic.ac.uk',email_verified=True),
    )



def test_verified_shortcode_immediately_maps_to_committee_without_union_request(monkeypatch):
    seen=[]
    async def get(path, params=None):
        seen.append(path)
        assert path=='/external-societies/for-login/ab123'
        return [{'id':9,'name':'Named society','permissions':['events.bookings.view'],'managing':False}]
    monkeypatch.setattr(database,'get',get)
    result=run(societies.affiliations(session('AB123@imperial.ac.uk')))
    assert result[0]['id']==9
    assert len(seen)==1


def test_verified_personal_email_from_union_roster_can_open_named_committee(monkeypatch):
    async def get(path, params=None):
        if path=='/external-societies/for-user/27/memberships':
            return [{'society_id':9,'cid':'trusted-cid','login':'ab123'}]
        if path=='/external-societies/for-login/ab123':
            return [{'id':9,'name':'Named society','permissions':['events.bookings.view'],'managing':False}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    results=run(societies.affiliations(session('verified.personal@example.com')))
    assert results[0]['id']==9


def test_verified_imperial_display_address_uses_official_union_login(monkeypatch):
    async def get(path,params=None):
        if path=='/external-societies/for-user/27/memberships':
            return [{'login':'ab123','cid':'official'}]
        if path=='/external-societies/for-login/ab123':return [{'id':9}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    assert run(societies.affiliations(session('firstname.lastname@imperial.ac.uk')))==[{'id':9}]


def test_nonmember_personal_email_never_inherits_untrusted_shortcode(monkeypatch):
    async def get(path,params=None):
        assert path=='/external-societies/for-user/27/memberships'
        return []
    monkeypatch.setattr(database,'get',get)
    account=session('verified.person@example.com')
    account.user.shortcode='ab123'  # typed at signup, never verified by Union
    assert run(societies.affiliations(account))==[]


def test_union_login_conflict_denies_access_without_guessing(monkeypatch):
    async def get(path,params=None):
        assert path=='/external-societies/for-user/27/memberships'
        return [{'login':'ab123'}, {'login':'xy456'}]
    monkeypatch.setattr(database,'get',get)
    with pytest.raises(HTTPException) as err:
        run(societies.affiliations(session('verified.person@example.com')))
    assert err.value.status_code==409


def test_unverified_account_never_accesses_committee_even_with_union_claim(monkeypatch):
    async def no_query(*args,**kwargs):raise AssertionError('Unverified lookup must not query private committee data')
    monkeypatch.setattr(database,'get',no_query)
    with pytest.raises(HTTPException) as err:
        run(societies.affiliations(session('ab123@ic.ac.uk',verified=False)))
    assert err.value.status_code==403


def test_impersonation_of_unverified_society_committee_short_code(monkeypatch):
    seen=[]
    async def get(path,params=None):
        seen.append(path)
        if path=='/external-societies/for-login/ab123':
            return [{'id':9,'name':'Robotics','permissions':['events.bookings.view'],'managing':True}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    impersonated=real_impersonation('ab123@ic.ac.uk')
    assert impersonated.is_impersonating and not impersonated.user.email_verified
    result=run(societies.affiliations(impersonated))
    assert len(result)==1 and result[0]['id']==9
    assert seen==['/external-societies/for-login/ab123']
    assert not impersonated.user.email_verified  # No account verification mutation.


def test_impersonated_unverified_imperial_display_email_uses_union_login(monkeypatch):
    async def get(path,params=None):
        if path=='/external-societies/lookup-member':
            assert params['email']=='firstname.lastname@imperial.ac.uk'
            return {'society_id':9,'login':'ab123','cid':'123456'}
        if path=='/external-societies/for-login/ab123':
            return [{'id':9,'name':'Robotics','managing':False}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    result=run(societies.affiliations(real_impersonation('firstname.lastname@imperial.ac.uk')))
    assert result[0]['name']=='Robotics'


def test_impersonated_unverified_personal_email_uses_exact_union_roster(monkeypatch):
    async def get(path,params=None):
        if path=='/external-societies/lookup-member':
            assert params['email']=='unverified-personal@example.com'
            return {'society_id':9,'login':'ab123','cid':'123456'}
        if path=='/external-societies/for-login/ab123':
            return [{'id':9,'name':'Robotics','managing':False}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    result=run(societies.affiliations(real_impersonation('unverified-personal@example.com')))
    assert result[0]['id']==9


def test_impersonated_account_without_society_assignment_stays_denied(monkeypatch):
    async def get(path,params=None):
        if path=='/external-societies/lookup-member':return None
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    impersonated=real_impersonation('not-on-roster@example.com')
    impersonated.user.shortcode='ab123'  # Unverified typed claim cannot grant access.
    assert run(societies.affiliations(impersonated))==[]
    with pytest.raises(HTTPException) as exc:
        run(societies.require_society(impersonated,9,'events.bookings.view'))
    assert exc.value.status_code==403


def test_impersonated_portal_route_returns_societies_but_not_other_society(monkeypatch):
    portal = importlib.import_module('routes.societies')
    async def get(path,params=None):
        if path=='/external-societies/for-login/ab123':
            return [{'id':9,'name':'Robotics', 'managing':True,
                     'permissions':['events.bookings.view']}]
        raise AssertionError(path)
    monkeypatch.setattr(database,'get',get)
    impersonated=real_impersonation('ab123@ic.ac.uk')
    result=run(portal.my_societies(session=impersonated))
    assert result==[{'id':9,'name':'Robotics','managing':True,
                     'permissions':['events.bookings.view']}]
    assert run(societies.require_society(impersonated,9,'events.bookings.view'))['id']==9
    with pytest.raises(HTTPException) as exc:
        run(societies.require_society(impersonated,10,'events.bookings.view'))
    assert exc.value.status_code==403


def test_impersonation_flag_cannot_be_spoofed_by_session_impersonator_id(monkeypatch):
    async def no_queries(*args,**kwargs):
        raise AssertionError('No private lookup for an ordinary unverified session')
    monkeypatch.setattr(database,'get',no_queries)
    fake=AuthSession(
        session=Session(token_hash='test',csrf_token='csrf',user_id=27,
                        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
                        impersonator_id=1),
        user=User(id=27,email='ab123@ic.ac.uk',email_verified=False),
        impersonator=None,
    )
    assert fake.is_impersonating is False
    with pytest.raises(HTTPException) as err:
        run(societies.affiliations(fake))
    assert err.value.status_code==403


def test_header_has_surfaced_link_and_no_silent_lookup_failure():
    header=(ROOT/'frontend/src/components/SiteHeader.vue').read_text()
    assert "label: 'Society committee'" in header
    assert 'societyLookupFailed.value = true' in header
    assert 'societyAccess.value || societyLookupFailed.value' in header
    assert "@media (max-width: 1450px)" in header
    portal=(ROOT/'frontend/src/router/index.js').read_text()
    assert "path: '/societies'" in portal
