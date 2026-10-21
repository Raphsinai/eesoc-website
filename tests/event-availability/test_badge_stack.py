"""Badge selection survives the real admin -> adapter -> public API flow.

Needs the isolated Docker compose test stack, never production.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def test_admin_badge_persists_and_event_edits_cannot_reset_it(admin_client, api):
    slug = f'badge-test-{uuid4().hex[:10]}'
    starts = datetime.now(timezone.utc) + timedelta(days=15)
    payload = {
        'slug': slug,
        'title': 'Card badge round trip',
        'starts_at': starts.isoformat(),
        'ends_at': (starts + timedelta(hours=2)).isoformat(),
        'sales_open_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'published': True,
        'ticket_types': [{
            'name': 'Standard', 'price_pence': 1500, 'capacity': 12,
            'requires_purchase': True,
        }],
    }
    response = admin_client.post('/admin/events', json=payload)
    assert response.status_code == 200, response.text
    event_id = response.json()['id']
    try:
        assert response.json()['availability_badge'] == 'auto'
        page = api.get(f'/events/{slug}')
        assert page.status_code == 200, page.text
        assert page.json()['status'] == 'On sale'
        assert 'places' not in page.json()['status'].lower()

        set_label = admin_client.patch(
            f'/admin/events/{event_id}/availability-badge',
            json={'availability_badge': 'not_many_left'},
        )
        assert set_label.status_code == 200, set_label.text
        assert set_label.json()['availability_badge'] == 'not_many_left'
        assert api.get(f'/events/{slug}').json()['status'] == 'Not many left'

        # The main event-edit payload is a separate action and must preserve the label.
        payload['title'] = 'Card badge round trip edited'
        payload['ticket_types'][0]['id'] = response.json()['ticket_types'][0]['id']
        renamed = admin_client.put(f'/admin/events/{event_id}', json=payload)
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['availability_badge'] == 'not_many_left'
        assert api.get(f'/events/{slug}').json()['status'] == 'Not many left'

        fast = admin_client.patch(f'/admin/events/{event_id}/availability-badge',
                                  json={'availability_badge': 'selling_fast'})
        assert fast.status_code == 200, fast.text
        assert api.get(f'/events/{slug}').json()['status'] == 'Selling fast'

        invalid = admin_client.patch(f'/admin/events/{event_id}/availability-badge',
                                     json={'availability_badge': '13 places'})
        assert invalid.status_code == 422, invalid.text
        assert api.get(f'/events/{slug}').json()['status'] == 'Selling fast'

        reset = admin_client.patch(f'/admin/events/{event_id}/availability-badge',
                                   json={'availability_badge': 'auto'})
        assert reset.status_code == 200, reset.text
        assert api.get(f'/events/{slug}').json()['status'] == 'On sale'
        assert any(e['slug'] == slug and e['status'] == 'On sale'
                   for e in api.get('/events').json()['events'])
    finally:
        admin_client.delete(f'/admin/events/{event_id}')


def test_auto_low_stock_at_nine_places_not_ten(admin_client, api):
    slug = f'badge-low-{uuid4().hex[:10]}'
    starts = datetime.now(timezone.utc) + timedelta(days=10)
    payload = {
        'slug': slug, 'title': 'Almost full',
        'starts_at': starts.isoformat(),
        'sales_open_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        'published': True,
        'ticket_types': [{
            'name': 'Standard', 'price_pence': 1200, 'capacity': 9,
            'requires_purchase': True,
        }],
    }
    result = admin_client.post('/admin/events', json=payload)
    assert result.status_code == 200, result.text
    try:
        assert api.get(f'/events/{slug}').json()['status'] == 'Not many left'
    finally:
        admin_client.delete(f"/admin/events/{result.json()['id']}")
