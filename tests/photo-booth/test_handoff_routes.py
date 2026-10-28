"""Short print links and draft framing stay within their permission boundaries."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/api'))
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:8081')
from routes import photos


@pytest.mark.parametrize('status', ['draft', 'published'])
def test_short_link_requires_a_published_print(monkeypatch, status):
    async def get(path):
        assert path == '/photo-sets/by-code/testabcdefgh'
        return {'token': 'long-private-token', 'status': status}

    monkeypatch.setattr(photos.database, 'get', get)
    if status == 'draft':
        with pytest.raises(HTTPException) as error:
            asyncio.run(photos.short_photo_link('testabcdefgh'))
        assert error.value.status_code == 404
    else:
        result = asyncio.run(photos.short_photo_link('testabcdefgh'))
        assert result.status_code == 302
        assert result.headers['location'].endswith('/photos/long-private-token')
        assert result.headers['cache-control'] == 'no-store'


def test_framing_write_is_scoped_to_exact_endpoint(monkeypatch):
    seen = []

    async def patch(path, json):
        seen.append((path, json))
        return {'id': 12, 'token': 'private-token', 'title': 'Event',
                'orientation': 'portrait', 'status': 'draft',
                'created_at': '2026-10-27T12:00:00+00:00', 'images': [],
                'placements': json['placements']}

    monkeypatch.setattr(photos.database, 'patch', patch)
    payload = photos.UpdateFraming(placements=[photos.PhotoPlacement(x=.4, y=.8, zoom=2)])
    result = asyncio.run(photos.edit_framing(12, payload))
    assert seen == [('/photo-sets/12/framing', {'placements': [{'x': .4, 'y': .8, 'zoom': 2.0}]})]
    assert result['placements'][0]['zoom'] == 2


@pytest.mark.parametrize('field,value', [('x', -1), ('y', 1.3), ('zoom', 9), ('zoom', float('nan'))])
def test_reject_invalid_placement(field, value):
    with pytest.raises(ValueError):
        photos.PhotoPlacement(**{field: value})
