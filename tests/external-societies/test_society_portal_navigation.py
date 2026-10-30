"""A named society committee can find its portal with verified Union aliases."""
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
database = importlib.import_module('clients.database')
run = asyncio.run


def session(address, verified=True, member=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=27,email=address,email_verified=verified,member_id=getattr(member,'id',None)),
        member=member,
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


def test_header_has_surfaced_link_and_no_silent_lookup_failure():
    header=(ROOT/'frontend/src/components/SiteHeader.vue').read_text()
    assert "label: 'Society committee'" in header
    assert 'societyLookupFailed.value = true' in header
    assert 'societyAccess.value || societyLookupFailed.value' in header
    assert "@media (max-width: 1450px)" in header
    portal=(ROOT/'frontend/src/router/index.js').read_text()
    assert "path: '/societies'" in portal
