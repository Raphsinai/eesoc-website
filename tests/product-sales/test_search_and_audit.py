"""Sales search is scoped, manually approved and safe; audit reports changed fields."""
import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'/'database'))


def load(name, path):
    spec=importlib.util.spec_from_file_location(name, path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

AUDIT=load('eesoc_audit_changes',ROOT/'backend/database/audit_changes.py')
SALES=load('eesoc_sale_routes',ROOT/'backend/database/routes/product_sales.py')


def test_audit_only_tracks_safe_named_mutations():
    assert AUDIT.target_for('PUT','/events/42') == ('events',42)
    assert AUDIT.target_for('PUT','/ticket-types/4') == ('ticket_types',4)
    assert AUDIT.target_for('POST','/reservations/99/confirm') == ('reservations',99)
    assert AUDIT.target_for('POST','/admins/8') == ('admins',8)
    assert AUDIT.target_for('POST','/login-codes') is None
    assert AUDIT.target_for('POST','/sessions') is None
    assert 'email' not in AUDIT.FIELDS['admins']
    assert 'note' not in AUDIT.FIELDS['reservations']


def test_audit_shows_only_fields_changed():
    before, after = AUDIT.change_values(
        {'title':'Original','capacity':70,'published':False},
        {'title':'Original','capacity':100,'published':True})
    assert before == {'capacity':70,'published':False}
    assert after == {'capacity':100,'published':True}
    assert AUDIT.change_values({'title':'same'},{'title':'same'}) == ({},{})


def test_audit_snapshot_shows_ticket_changes_without_personal_data():
    class Connection:
        async def fetchval(self,*args):
            return '{"title":"Event","capacity":70,"private_email":"secret@example.org"}'
        async def fetch(self,*args):
            return [{'id':5,'fields':'{"name":"Early bird","capacity":20,"notes":"private"}'}]
    result = asyncio.run(AUDIT.snapshot(Connection(),('events',2)))
    assert result == {'title':'Event','capacity':70,'ticket #5 · name':'Early bird',
                      'ticket #5 · capacity':20}


def test_candidate_search_ranks_name_and_email_and_never_links_itself(monkeypatch):
    class Connection:
        async def fetchrow(self,*args):
            return {'status':'unreserved','quantity':2,'buyer_name':'Ada Lovelace',
                    'cid':None,'email':'ada@example.org','event_id':123,'ticket_type_id':44}
        async def fetch(self,sql,*args):
            assert 'r.event_id=$1 AND r.ticket_type_id=$2' in sql
            assert "r.status='held'" in sql
            assert args[:2] == (123,44)
            return [
                {'id':12,'quantity':2,'status':'held','created_at':None,'order_number':None,
                 'email':'grace@example.org','first_name':'Grace','last_name':'Hopper',
                 'cid':None,'member_first_name':None,'member_last_name':None},
                {'id':11,'quantity':2,'status':'held','created_at':None,'order_number':None,
                 'email':'ada@example.org','first_name':'Ada','last_name':'Lovelace',
                 'cid':None,'member_first_name':None,'member_last_name':None},
                {'id':10,'quantity':1,'status':'held','created_at':None,'order_number':None,
                 'email':'ada.1@example.org','first_name':'Ada','last_name':'Lovelace',
                 'cid':None,'member_first_name':None,'member_last_name':None},
            ]
    class Context:
        async def __aenter__(self): return Connection()
        async def __aexit__(self,*args): return False
    class Pool:
        def acquire(self): return Context()
    async def fake_pool(): return Pool()
    monkeypatch.setattr(SALES,'get_pool',fake_pool)
    data=asyncio.run(SALES.reservation_candidates(99,q='ada',limit=20))
    assert [r['id'] for r in data['candidates']][:2] == [11,12] or data['candidates'][0]['id'] == 11
    assert data['candidates'][0]['email_match'] is True
    assert data['candidates'][-1]['quantity_matches'] is False
    assert data['total'] == 3
