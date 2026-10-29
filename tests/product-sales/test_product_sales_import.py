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
sale_source_key = module.sale_source_key


class SaleRow:
    def __init__(self, **kwargs):
        self.cid = kwargs.get('cid')
        self.buyer_name = kwargs.get('buyer_name')
        self.quantity = kwargs.get('quantity', 1)
        self.order_number = kwargs.get('order_number')
        self.product_line_id = kwargs.get('product_line_id')
        self.source_row = kwargs.get('source_row', 2)


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
    assert "row['status'] not in ('review', 'unreserved')" in db_route
    assert 'reservation_candidates' in db_route
    assert 'This Union order already confirms another reservation' in db_route
    assert "FOR UPDATE" in db_route
    assert "status='ticket_added'" in (ROOT / 'backend' / 'database' / 'routes' / 'events.py').read_text()


def test_missing_cid_never_matches_explicit_zero_cid():
    # Before this regression fix, normalized_cid(None) and normalized_cid('0000')
    # both returned '0', silently confirming a reservation with no known CID.
    rows = [held(21, 'Other Buyer', cid=None)]
    status, match, _, _ = choose_reservation(sale(cid='0000', buyer_name='Other Buyer'), rows, set())
    assert status == 'unreserved' and match is None
    status, match, _, _ = choose_reservation(sale(cid='0'), [held(22, 'Zero', cid='0000')], set())
    assert status == 'confirmed' and match['id'] == 22


def test_alphanumeric_cid_is_not_loosely_stripped():
    rows = [held(21, 'Other Buyer', cid='ABC0123')]
    status, match, _, _ = choose_reservation(sale(cid='000ABC0123'), rows, set())
    assert status == 'unreserved' and match is None


def test_union_order_duplicate_keys_are_scoped_to_ticket_type_and_line():
    first = sale(order_number='ORD-1234', product_line_id=77)
    second = sale(order_number='ord-1234 ', product_line_id=77)
    assert sale_source_key(100, 10, first, 'a' * 64) == sale_source_key(100, 10, second, 'b' * 64)
    assert sale_source_key(100, 10, first, 'a' * 64) != sale_source_key(100, 11, first, 'a' * 64)
    assert sale_source_key(100, 10, first, 'a' * 64) != sale_source_key(100, 10,
        sale(order_number='ORD-1234', product_line_id=78), 'a' * 64)


def test_import_marks_matching_pending_order_verification_as_matched():
    source = (ROOT / 'backend' / 'database' / 'routes' / 'product_sales.py').read_text()
    assert "UPDATE public.reservation_order_verification" in source
    assert "AND status='pending'" in source
    assert "status='matched'" in source
    assert source.count('await _record_verified_import_order(') == 2
    assert 'confirmed.ticket_type_id=$3' in source


def test_sums_sales_filename_accepts_visible_shop_id_and_api_id():
    from routes.product_sales import product_id_for_sales_export
    targets = [{'product_id': 1003023, 'ticket_type_id': 7}]
    assert product_ids_in_filename('Product_Sales_ID_3023.xlsx') == [3023]
    assert product_id_for_sales_export('Product_Sales_ID_3023.xlsx', targets) == 1003023
    assert product_id_for_sales_export('Product_Sales_ID_1003023.xlsx', targets) == 1003023
    # Keep IDs that came directly from the Union API, not manually entered.
    assert product_id_for_sales_export('product 502.csv', [{'product_id': 502}]) == 502


def test_sums_sales_filename_rejects_unknown_or_ambiguous_product():
    from routes.product_sales import product_id_for_sales_export
    targets = [{'product_id': 1003023}, {'product_id': 1003043}]
    with pytest.raises(HTTPException):
        product_id_for_sales_export('Product_Sales_ID_3024.xlsx', targets)
    with pytest.raises(HTTPException):
        product_id_for_sales_export('Product_3023_Product_3043.xlsx', targets)
