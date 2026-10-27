"""CSV and name-matching regression tests; no database or Union calls."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend' / 'api'))
from services.product_sales_import import parse_export, product_ids_in_filename

spec = importlib.util.spec_from_file_location('sale_matcher', ROOT / 'backend' / 'database' / 'sale_matching.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
choose_reservation = module.choose_reservation


class SaleRow:
    def __init__(self, **kwargs):
        self.cid = kwargs.get('cid')
        self.buyer_name = kwargs.get('buyer_name')
        self.quantity = kwargs.get('quantity', 1)


def test_filename_resolves_unambiguous_product_identifier():
    assert product_ids_in_filename('Product 78901 - Sales.csv') == [78901]
    assert product_ids_in_filename('Sales for product ID 453321.xlsx') == [453321]
    assert 453321 in product_ids_in_filename('Product 453321 - 2026_10.xlsx')
    assert product_ids_in_filename('sales.xlsx') == []


def test_csv_parser_tolerates_delimiter_bom_metadata_and_aliases():
    raw = ('Product Report\nOrder Number;CID;First Name;Surname;Qty;Email Address\n'
           'ORDER-ABC;00001234;Ada;Lovelace;2;ada@example.org\n'
           'ORDER-DEF;;Grace;Hopper;1;grace@example.org\n').encode('utf-8-sig')
    records = parse_export('Product 78901.csv', raw)
    assert len(records) == 2
    assert records[0]['cid'] == '00001234'
    assert records[0]['quantity'] == 2
    assert records[0]['buyer_name'] == 'Ada Lovelace'
    assert records[1]['cid'] is None
    assert records[1]['buyer_name'] == 'Grace Hopper'


def test_parser_rejects_unidentifiable_buyers_and_invalid_quantities():
    with pytest.raises(HTTPException):
        parse_export('Product 1234.csv', b'Name,Quantity\nJane Person,0\n')
    with pytest.raises(HTTPException):
        parse_export('Product 1234.csv', b'Not A Sales File,Total\n1,22\n')


def held(id, name, cid=None, quantity=1, status='held'):
    return dict(id=id, name=name, cid=cid, quantity=quantity, status=status)


def sale(**kwargs):
    return SaleRow(source_row=2, **kwargs)


def test_exact_cid_match_confirms_only_one_held_reservation():
    rows = [held(1, 'Other Name', '00001234'), held(2, 'Ada Lovelace', '00005678')]
    status, candidate, alternatives, reason = choose_reservation(sale(cid='00001234', buyer_name='Ada Lovelace'), rows, set())
    assert status == 'confirmed' and candidate['id'] == 1 and reason == 'cid'
    assert alternatives == []


def test_with_cid_does_not_fall_back_to_name_match():
    status, candidate, alternatives, reason = choose_reservation(
        sale(cid='unmatched', buyer_name='Ada Lovelace'), [held(1, 'Ada Lovelace', 'other')], set())
    assert status == 'unreserved' and candidate is None


def test_unique_exact_name_auto_confirms_but_close_name_goes_to_approval():
    rows = [held(1, 'Grace Hopper'), held(2, 'Ada Lovelace')]
    status, target, _, reason = choose_reservation(sale(buyer_name='Grace   HOPPER'), rows, set())
    assert (status, target['id'], reason) == ('confirmed', 1, 'exact_name')
    status, target, suggestions, reason = choose_reservation(sale(buyer_name='Grase Hopper'), rows, set())
    assert status == 'review' and target is None and suggestions[0]['id'] == 1
    assert reason == 'close_name'


def test_ambiguous_and_wrong_quantity_never_auto_confirm():
    same = [held(1, 'Ada Lovelace'), held(2, 'Ada Lovelace')]
    assert choose_reservation(sale(buyer_name='Ada Lovelace'), same, set())[0] == 'review'
    assert choose_reservation(sale(cid='123', quantity=2), [held(1, 'Person', '123')], set())[0] == 'review'


def test_no_reservation_is_queued_not_issued():
    status, target, suggestions, _ = choose_reservation(sale(buyer_name='New Buyer'), [], set())
    assert (status, target, suggestions) == ('unreserved', None, [])


def test_missing_or_partial_sale_cannot_auto_confirm_consumed_reservation():
    status, target, *_ = choose_reservation(sale(buyer_name='Ada Lovelace'), [held(1, 'Ada Lovelace')], {1})
    assert status == 'unreserved' and target is None


def test_leading_zeros_in_numeric_export_cid_do_not_change_the_person():
    rows = [held(42, 'Buyer Example', '00001234')]
    status, candidate, *_ = choose_reservation(sale(cid='1234'), rows, set())
    assert status == 'confirmed' and candidate['id'] == 42


def test_surname_comma_given_name_normalises_when_export_has_one_name_column():
    data = parse_export('Product 12345.csv', b'Name,Order Number\n"Lovelace, Ada",A-123\n')
    assert data[0]['buyer_name'] == 'Ada Lovelace'


def test_chosen_reservation_was_already_confirmed_then_import_must_not_auto_confirm_another():
    rows = [held(42, 'Ada Lovelace', status='confirmed'), held(43, 'Ada Lovelace')]
    result = choose_reservation(sale(buyer_name='Ada Lovelace'), rows, set())
    assert result[0] == 'review'


def test_approval_and_add_ticket_endpoints_are_separately_permissioned():
    source = (ROOT / 'backend' / 'api' / 'routes' / 'product_sales.py').read_text()
    assert "Depends(require_action('events.bookings.import'))" in source
    assert "Depends(require_action('events.bookings.confirm'))" in source
    assert "Depends(require_action('events.bookings.add'))" in source
    assert "session.can('events.bookings.confirm')" in source
    assert 'await event_for_import(' in source
    db_route = (ROOT / 'backend' / 'database' / 'routes' / 'product_sales.py').read_text()
    assert "reservation['status'] != 'held'" in db_route
    assert "if reservation_id not in" in db_route
    assert "FOR UPDATE" in db_route
    assert "status='ticket_added'" in (ROOT / 'backend' / 'database' / 'routes' / 'events.py').read_text()
