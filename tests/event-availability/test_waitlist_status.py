"""A sold-out event advertises Waitlist only while queue signups are usable."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'backend/api/utils/present.py'
spec = importlib.util.spec_from_file_location('utils.present_waitlist_badge', SOURCE)
present = importlib.util.module_from_spec(spec)
spec.loader.exec_module(present)


def iso(days=0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def full_event(**overrides):
    event = {
        'published': True,
        'slug': 'sold-out-event',
        'starts_at': iso(10),
        'sales_open_at': iso(-1),
        'sold_out': True,
        'waitlist_enabled': True,
        'ticket_types': [{'name': 'General', 'sold_out': True, 'remaining': 0,
                          'price_pence': 1000, 'sales_open_at': None}],
        'availability_badge': 'auto',
    }
    event.update(overrides)
    return event


def test_default_waitlist_badge_and_detail_consistent():
    data = full_event()
    assert present.waitlist_available(data)
    assert present.event_status(data) == ('Waitlist', 'accent')
    assert present.event_card(data)['status'] == 'Waitlist'


@pytest.mark.parametrize('override', [
    {'waitlist_enabled': False},
    {'published': False},
    {'sales_open_at': None},
    {'sales_open_at': iso(2)},
    {'reservations_close_at': iso(-1)},
    {'signup_url': 'https://example.com/signup'},
    {'ticket_types': []},
])
def test_waitlist_disabled_for_unavailable_flows(override):
    assert not present.waitlist_available(full_event(**override))


def test_sold_out_uses_plain_status_when_waitlist_off():
    assert present.event_status(full_event(waitlist_enabled=False)) == ('Sold out', 'danger')
    assert present.event_status(full_event(reservations_close_at=iso(-1))) == ('Sold out', 'danger')
    # Manual labels are marketing only. They never hide a full event.
    assert present.event_status(full_event(waitlist_enabled=False, availability_badge='selling_fast')) == ('Sold out', 'danger')
    assert present.event_status(full_event(availability_badge='not_many_left')) == ('Waitlist', 'accent')


def test_not_full_never_says_waitlist_even_if_toggle_is_on():
    event = full_event(sold_out=False, remaining=20,
                       ticket_types=[{'name': 'General', 'sold_out': False,
                                      'remaining': 20, 'price_pence': 1000}])
    assert not present.waitlist_available(event)
    assert present.event_status(event) == ('On sale', 'success')
