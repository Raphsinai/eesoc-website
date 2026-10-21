"""Real waitlist on/off round-trip through public, admin and DB adapter API.

Runs with the isolated compose test stack, never production.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def test_waitlist_when_full_and_admin_can_disable_reenable(admin_client, member_client, api):
    slug = f'waitlist-status-{uuid4().hex[:12]}'
    now = datetime.now(timezone.utc)
    starts = now + timedelta(days=10)
    payload = {
        'slug': slug,
        'title': 'Waitlist badge integration',
        'starts_at': starts.isoformat(),
        'ends_at': (starts + timedelta(hours=3)).isoformat(),
        'sales_open_at': (now - timedelta(days=1)).isoformat(),
        'published': True,
        'ticket_types': [
            {'name': 'Standard', 'price_pence': 0, 'requires_purchase': False, 'capacity': 1},
        ],
    }
    result = admin_client.post('/admin/events', json=payload)
    assert result.status_code == 200, result.text
    event = result.json()
    try:
        ticket_id = event['ticket_types'][0]['id']
        def view():
            response = api.get(f'/events/{slug}')
            assert response.status_code == 200, response.text
            return response.json()
        # A free ticket with only one seat triggers the <10 low-stock label.
        assert view()['status'] == 'Not many left'
        taken = member_client.post(f'/events/{slug}/reserve', json={
            'ticket_type_id': ticket_id, 'quantity': 1,
        })
        assert taken.status_code == 200, taken.text
        assert taken.json()['reservation']['status'] == 'confirmed'
        assert view()['status'] == 'Waitlist'
        assert view()['waitlistAvailable'] is True
        assert any(e['slug'] == slug and e['status'] == 'Waitlist'
                   for e in api.get('/events').json()['events'])

        # Different account can join while full.
        joined = admin_client.post(f'/events/{slug}/waitlist', json={
            'ticket_type_id': ticket_id, 'quantity': 1,
        })
        assert joined.status_code == 200, joined.text
        assert joined.json()['ok']
        assert admin_client.post(f'/events/{slug}/waitlist', json={}).json()['already']

        disabled = admin_client.patch(f"/admin/events/{event['id']}/waitlist-enabled",
                                      json={'waitlist_enabled': False})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()['waitlist_enabled'] is False
        assert view()['status'] == 'Sold out'
        assert view()['waitlistAvailable'] is False
        refused = admin_client.post(f'/events/{slug}/waitlist', json={})
        assert refused.status_code == 409, refused.text
        # Disabling new joins does not throw away the existing queue.
        waiting = admin_client.get(f"/admin/waitlist?event_id={event['id']}")
        assert waiting.status_code == 200, waiting.text
        assert any(w['user_id'] for w in waiting.json())

        restored = admin_client.patch(f"/admin/events/{event['id']}/waitlist-enabled",
                                      json={'waitlist_enabled': True})
        assert restored.status_code == 200, restored.text
        assert view()['status'] == 'Waitlist'

        payload['reservations_close_at'] = (now - timedelta(minutes=2)).isoformat()
        payload['ticket_types'][0]['id'] = ticket_id
        ended = admin_client.put(f"/admin/events/{event['id']}", json=payload)
        assert ended.status_code == 200, ended.text
        assert view()['status'] == 'Sold out'
        assert view()['waitlistAvailable'] is False
        assert admin_client.post(f'/events/{slug}/waitlist', json={}).status_code == 409
    finally:
        admin_client.delete(f"/admin/events/{event['id']}")
