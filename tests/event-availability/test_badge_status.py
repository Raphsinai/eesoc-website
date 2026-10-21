"""Public event-card labels never leak remaining counts; overrides are cosmetic."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'backend/api/utils/present.py'
spec = importlib.util.spec_from_file_location('utils.present_availability', SOURCE)
present = importlib.util.module_from_spec(spec)
spec.loader.exec_module(present)


def iso(days=0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def ticket(remaining, price=1200, **extra):
    return { 'name': 'Standard', 'remaining': remaining, 'sold_out': remaining == 0,
             'price_pence': price, 'members_only': False, **extra }


def event(*types, **extra):
    return { 'published': True, 'sales_open_at': iso(-1), 'ticket_types': list(types),
             'starts_at': iso(7), 'availability_badge': 'auto', **extra }


@pytest.mark.parametrize('remaining, expected', [(None, 'On sale'), (50, 'On sale'),
                                               (10, 'On sale'), (9, 'Not many left'),
                                               (1, 'Not many left')])
def test_automatic_threshold_is_strictly_below_ten(remaining, expected):
    assert present.event_status(event(ticket(remaining))) == (
        expected, 'accent' if expected == 'Not many left' else 'success')


def test_public_api_card_contains_only_qualitative_status():
    card = present.event_card(event(ticket(6), title='Halloween', slug='halloween'))
    assert card['status'] == 'Not many left'
    assert 'remaining' not in card and 'capacity' not in card
    assert not any(ch.isdigit() for ch in card['status'])


def test_manual_badges_work_without_calculated_inventory():
    assert present.event_status(event(ticket(None), availability_badge='not_many_left'))[0] == 'Not many left'
    assert present.event_status(event(ticket(30), availability_badge='selling_fast'))[0] == 'Selling fast'
    # A manual Selling fast setting deliberately takes priority over auto-low-stock.
    assert present.event_status(event(ticket(4), availability_badge='selling_fast'))[0] == 'Selling fast'


def test_unlimited_ticket_does_not_trigger_false_low_stock():
    assert present.event_status(event(ticket(5), ticket(None))) == ('On sale', 'success')
    # The event-wide cap still applies to every ticket type, including unlimited ones.
    assert present.event_status(event(ticket(5), ticket(None), remaining=7))[0] == 'Not many left'


def test_only_the_active_tickets_count_and_the_global_cap_is_respected():
    assert present.event_status(event(ticket(2, sales_close_at=iso(-1)), ticket(11))) == ('On sale', 'success')
    assert present.event_status(event(ticket(2), ticket(50, sales_open_at=iso(2))))[0] == 'Not many left'
    assert present.event_status(event(ticket(18), ticket(15), remaining=8))[0] == 'Not many left'


@pytest.mark.parametrize('other, expected', [
    ({ 'sold_out': True }, ('Waitlist', 'accent')),
    ({ 'sales_open_at': None }, ('On sale soon', 'neutral')),
    ({ 'sales_open_at': iso(1) }, None),
    ({ 'reservations_close_at': iso(-1) }, ('Booking closed', 'neutral')),
    ({ 'published': False }, ('Draft', 'neutral')),
])
def test_terminal_and_scheduled_states_take_priority_over_badge(other, expected):
    data = event(ticket(8))
    data.update(other)
    data['availability_badge'] = 'selling_fast'
    status = present.event_status(data)
    if expected is None:
        assert status[0].startswith('On sale ')
    else:
        assert status == expected


def test_free_and_members_only_stay_informative():
    assert present.event_status(event(ticket(20, price=0))) == ('Free entry', 'success')
    assert present.event_status(event(ticket(20, members_only=True))) == ('Members only', 'brand')
    assert present.event_status(event(ticket(5, price=0)))[0] == 'Not many left'
