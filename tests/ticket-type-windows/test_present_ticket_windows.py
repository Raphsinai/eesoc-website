"""Ticket type sale windows in ``present.py`` (migration 080).

Pure unit tests — no test stack. ``backend/api/utils/present.py`` imports only
the standard library by contract, so it is loaded straight off the filesystem,
as the doors-close tests do.
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRESENT = Path(__file__).resolve().parents[2] / "backend" / "api" / "utils" / "present.py"
_spec = importlib.util.spec_from_file_location("utils.present_ticket_windows", PRESENT)
present = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(present)


def _iso(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _event(*types, **extra) -> dict:
    return {
        "published": True,
        "sales_open_at": _iso(-48),
        "ticket_types": list(types),
        **extra,
    }


def _tt(name="Seat", price=500, **extra) -> dict:
    return {"name": name, "price_pence": price, "capacity": 20, "remaining": 20,
            "sold_out": False, **extra}


def test_no_times_of_its_own_is_open():
    assert present.ticket_sale_state(_tt()) == "open"
    assert present.ticket_sale_state(_tt(sales_open_at=None, sales_close_at=None)) == "open"


def test_before_its_window_is_scheduled_and_after_it_is_ended():
    assert present.ticket_sale_state(_tt(sales_open_at=_iso(1))) == "scheduled"
    assert present.ticket_sale_state(_tt(sales_close_at=_iso(-1))) == "ended"
    assert present.ticket_sale_state(
        _tt(sales_open_at=_iso(-1), sales_close_at=_iso(1))
    ) == "open"


def test_a_naive_time_is_utc():
    naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    assert present.ticket_sale_state(_tt(sales_open_at=naive.isoformat())) == "scheduled"


def test_the_next_opening_is_the_soonest_scheduled_type():
    soon, later = _iso(2), _iso(30)
    types = [_tt("A", sales_open_at=later), _tt("B", sales_open_at=soon), _tt("C")]
    assert present.next_ticket_opening(types) == datetime.fromisoformat(soon)
    assert present.next_ticket_opening([_tt()]) is None


def test_the_badge_is_unchanged_without_windows():
    assert present.event_status(_event(_tt()))[0] == "20 places"
    assert present.event_status(_event(_tt(price=0)))[0] == "Free entry"


def test_between_early_bird_and_general_the_badge_says_when():
    event = _event(
        _tt("Early bird", sales_close_at=_iso(-1)),
        _tt("General", sales_open_at=_iso(26)),
    )
    label, tone = present.event_status(event)
    assert label.startswith("On sale ") and tone == "accent"


def test_early_bird_sold_out_before_general_opens_says_when_too():
    event = _event(
        _tt("Early bird", remaining=0, sold_out=True),
        _tt("General", sales_open_at=_iso(26)),
    )
    assert present.event_status(event)[0].startswith("On sale ")


def test_every_type_ended_reads_as_booking_closed():
    event = _event(_tt("Early bird", sales_close_at=_iso(-1)))
    assert present.event_status(event) == ("Booking closed", "neutral")


def test_the_badge_counts_only_what_is_on_sale():
    # Early bird ended with 20 left; general on sale with 5 left.
    event = _event(
        _tt("Early bird", sales_close_at=_iso(-1)),
        _tt("General", sales_open_at=_iso(-1), remaining=5),
    )
    assert present.event_status(event)[0] == "5 places"
