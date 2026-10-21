"""Imperial students should wait for daily sync, not provide an order instantly."""
import os
import sys
from pathlib import Path

os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:9')
API_DIR = Path(__file__).resolve().parents[2] / 'backend' / 'api'
sys.path.insert(0, str(API_DIR))
from routes import events


def test_imperial_student_without_synced_society_membership_is_told_to_wait_for_matching():
    step = events._next_step({'requires_purchase': True}, is_member=False,
                             as_guest=False, imperial_account=True)
    assert step['action'] == 'buy'
    assert '24 hours' in step['message']
    assert 'order number' in step['message']
    assert 'same Imperial account' in step['message']


def test_imperial_member_has_same_24_hour_guidance():
    step = events._next_step({'requires_purchase': True}, is_member=True,
                             as_guest=False, imperial_account=True)
    assert step['action'] == 'buy'
    assert '24 hours' in step['message']


def test_guest_imperial_student_gets_signin_fallback_after_daily_sync():
    step = events._next_step({'requires_purchase': True}, is_member=False,
                             as_guest=True, imperial_account=True)
    assert '24 hours' in step['message']
    assert 'code we' in step['message']


def test_non_imperial_account_still_gets_order_number_instruction():
    step = events._next_step({'requires_purchase': True}, is_member=False,
                             as_guest=False, imperial_account=False)
    assert step['action'] == 'buy_and_supply_order'
    assert 'order number' in step['message']


def test_free_bookings_do_not_ask_for_order_number():
    step = events._next_step({'requires_purchase': False}, is_member=True,
                             as_guest=False, status='confirmed', imperial_account=True)
    assert step['action'] == 'none'
    assert '24 hours' not in step['message']


def test_imperial_order_input_is_hidden_until_explicit_cta():
    vue = (Path(__file__).resolve().parents[2] / 'frontend/src/views/EventView.vue').read_text()
    assert 'const imperialStudent = computed(' in vue
    assert 'const expandedOrderForms = ref({})' in vue
    assert "data-testid=\"order-number-toggle\"" in vue
    assert "v-if=\"expandedOrderForms[r.id]\"" in vue
    assert "24 hours after purchase" in vue
