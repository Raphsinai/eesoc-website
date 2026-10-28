"""Photo-booth persistence, short-link privacy and action boundary checks."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend' / 'api'))
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:8081')
from routes import photos
from utils.roles import PERMISSIONS


QR_CODE = 'abcdefgh1234'
TOKEN = 'abcdefghijklmnopqrstuvwxyz123456'


def draft(status='draft'):
    return {
        'id': 31, 'token': TOKEN, 'qr_code': QR_CODE,
        'title': 'Careers Fair', 'print_label': 'Careers Fair', 'event_id': 7,
        'event_date': 'Wed 7 Oct 2026', 'event_venue': 'EEE Building',
        'orientation': 'portrait', 'status': status, 'printed_at': None,
        'created_at': '2026-10-07T10:00:00Z', 'published_at': None,
        'placements': [{'x': .2, 'y': .8, 'zoom': 1.5}],
        'images': [{'id': 100, 'stored_name': 'media/a.jpg'}],
    }


def session():
    return SimpleNamespace(user=SimpleNamespace(id=42), can=lambda _action: True)


def test_template_framaing_is_separately_permitted():
    assert 'photos.framing.edit' in PERMISSIONS
    route = next(r for r in photos.router.routes if r.path == '/admin/photos/sets/{set_id}/framing')
    assert route.methods == {'PATCH'}
    assert 'photos.framing.edit' in Path(ROOT / 'backend/api/routes/photos.py').read_text()


def test_create_rejects_mismatch_and_outside_photo_crop_bounds():
    with pytest.raises(ValidationError):
        photos.CreatePhotos(event_id=7, image_ids=[100, 101], placements=[photos.PhotoPlacement()])
    for field, number in [('x', -1), ('x', 2), ('y', -1), ('y', 2), ('zoom', .9), ('zoom', 8.1), ('zoom', float('nan'))]:
        with pytest.raises(ValidationError):
            photos.PhotoPlacement(**{field: number})


def test_create_snapshots_framing_and_uses_short_qr_code(monkeypatch):
    sent = []

    async def fake_get(path):
        assert path == '/events/7'
        return {'id': 7, 'published': True, 'title': 'Careers Fair'}

    async def fake_post(path, body):
        assert path == '/photo-sets'
        sent.append(body)
        return draft()

    monkeypatch.setattr(photos.database, 'get', fake_get)
    monkeypatch.setattr(photos.database, 'post', fake_post)
    payload = photos.CreatePhotos(event_id=7, image_ids=[100],
                                  placements=[{'x': .2, 'y': .8, 'zoom': 1.5}])
    result = asyncio.run(photos.create_set(payload, session=session()))
    assert sent[0]['placements'] == [{'x': .2, 'y': .8, 'zoom': 1.5}]
    assert sent[0]['image_ids'] == [100]
    assert 12 <= len(sent[0]['qr_code']) <= 16
    assert result['eventDate'] == 'Wed 7 Oct 2026'
    assert result['placements'][0]['zoom'] == 1.5
    assert 'url' not in result and 'printUrl' not in result
    assert '/api/p/' in result['previewQrUrl']


def test_framing_patch_roundtrips_and_restricts_action(monkeypatch):
    calls = []
    async def fake_patch(path, payload):
        calls.append((path, payload))
        return draft()
    monkeypatch.setattr(photos.database, 'patch', fake_patch)
    result = asyncio.run(photos.edit_framing(31, photos.UpdateFraming(placements=[
        photos.PhotoPlacement(x=.2,y=.8,zoom=1.5),
    ])))
    assert calls == [('/photo-sets/31/framing', {'placements':[{'x':.2,'y':.8,'zoom':1.5}]})]
    assert result['status'] == 'draft'


def test_public_short_link_rejects_draft_and_redirects_published(monkeypatch):
    async def fake_get(path):
        assert path == f'/photo-sets/by-code/{QR_CODE}'
        return draft(status)
    for status in ('draft', 'published'):
        monkeypatch.setattr(photos.database, 'get', fake_get)
        if status == 'draft':
            with pytest.raises(HTTPException) as error:
                asyncio.run(photos.short_photo_link(QR_CODE))
            assert error.value.status_code == 404
        else:
            response = asyncio.run(photos.short_photo_link(QR_CODE))
            assert response.status_code == 302
            assert response.headers['location'].endswith('/photos/' + TOKEN)
            assert response.headers['Cache-Control'] == 'no-store'
    assert photos._qr_url({**draft(), 'qr_code': None}).endswith('/photos/'+TOKEN)
