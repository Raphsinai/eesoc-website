"""Combined door screens must not expand an officer's event scope."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:9')
sys.path.insert(0, str(ROOT / 'backend' / 'api'))
checkin = importlib.import_module('routes.checkin')
society_actions = importlib.import_module('routes.society_event_actions')
database = importlib.import_module('clients.database')
societies = importlib.import_module('services.societies')
run = asyncio.run


def test_selected_ids_are_bounded_and_distinct():
    assert checkin.validate_event_ids([1, 2]) == [1, 2]
    for ids in ([], [1, 1], [0, 1], [-1, 2], list(range(1, 22))):
        with pytest.raises(HTTPException) as err:
            checkin.validate_event_ids(ids)
        assert err.value.status_code == 422


def test_multiple_event_ticket_verdict():
    row = {'event_id': 9, 'status': 'confirmed', 'paid': True,
           'event_title': 'Second listing'}
    assert checkin._verdict(row, None, [7, 9])[0] == 'ok'
    assert checkin._verdict(row, None, [7, 8])[0] == 'wrong_event'
    assert checkin._verdict(row, 7)[0] == 'wrong_event'


def test_combined_scan_checks_all_selected_event_scopes_before_ticket(monkeypatch):
    events = []
    async def get(path, params=None):
        if path.startswith('/events/'):
            event_id = int(path.rsplit('/', 1)[1]); events.append(event_id)
            return {'id': event_id, 'industry': event_id == 2}
        raise AssertionError(path)
    async def booking(_):
        raise AssertionError('ticket must not be read until selected scope verified')
    monkeypatch.setattr(database, 'get', get)
    monkeypatch.setattr(checkin, '_booking', booking)
    session = SimpleNamespace(can=lambda key: key == 'events.scope.industry')
    with pytest.raises(HTTPException) as err:
        run(checkin.scan(token='test', event_id=None, event_ids=[2, 1], session=session))
    assert err.value.status_code == 403
    assert events == [2, 1]


def test_society_scan_does_not_read_foreign_ticket(monkeypatch):
    calls = []
    async def require_society(session, society_id, action, managing=True):
        assert action == 'events.checkin.scan'
        return {'id': society_id, 'permissions': [action], 'managing': True}
    async def require_event(society_id, event_id):
        calls.append(event_id)
        if event_id == 99:
            raise HTTPException(403, 'Not assigned')
    async def booking(_):
        return {'id': 55, 'event_id': 99}
    async def never(**kwargs):
        raise AssertionError('foreign ticket reached scanner')
    monkeypatch.setattr(societies, 'require_society', require_society)
    monkeypatch.setattr(societies, 'require_event', require_event)
    monkeypatch.setattr(society_actions.tickets, 'parse', lambda token: 55)
    monkeypatch.setattr(checkin, '_booking', booking)
    monkeypatch.setattr(checkin, 'scan', never)
    with pytest.raises(HTTPException) as err:
        run(society_actions.combined_checkin_scan(3, 'token', [2, 3], SimpleNamespace()))
    assert err.value.status_code == 403
    assert calls == [2, 3, 99]
