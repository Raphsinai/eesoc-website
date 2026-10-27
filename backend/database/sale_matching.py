"""Strict CID and name matching with a fuzzy approval-only path.

Intentionally pure so matching behaviour can be tested without a running DB.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from hashlib import sha256

def normalized_name(value: str | None) -> str:
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(x for x in value if not unicodedata.combining(x))
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))


def normalized_cid(value: str | None) -> str:
    # Missing CIDs must not become the same value as an all-zero CID. Only
    # *numeric* CIDs drop leading zeros; alphabetic IDs are preserved verbatim.
    raw = re.sub(r'\s+', '', value or '').casefold()
    return (raw.lstrip('0') or '0') if raw.isdecimal() else raw


def sale_source_key(product_id: int, ticket_type_id: int, sale, file_sha256: str) -> str:
    # An order may contain more than one ticket type for the same Union product.
    # Both product and ticket type must be part of the identity; otherwise the
    # second valid line is mislabelled a duplicate of the first one.
    if sale.order_number:
        identity = (f'order:{product_id}:{ticket_type_id}:'
                    f'{sale.product_line_id or 0}:{sale.order_number.strip().casefold()}')
    else:
        identity = f'file:{file_sha256}:{sale.source_row}'
    return sha256(identity.encode()).hexdigest()


def choose_reservation(sale, reservations: list[dict], already_used: set[int]) -> tuple[str, dict | None, list[dict], str]:
    # Supplying a CID disallows fallback to matching a different person's name.
    candidates = [r for r in reservations if r['id'] not in already_used]
    if sale.cid:
        cid = normalized_cid(sale.cid)
        hits = [r for r in candidates if cid and normalized_cid(r.get('cid')) == cid]
        matched_on = 'cid'
    else:
        exact = normalized_name(sale.buyer_name)
        hits = [r for r in candidates if exact and normalized_name(r.get('name')) == exact]
        matched_on = 'exact_name'
    held = [r for r in hits if r['status'] == 'held']
    if len(hits) == 1 and len(held) == 1 and held[0]['quantity'] == sale.quantity:
        return 'confirmed', held[0], [], matched_on
    if hits:
        options = [{'id': r['id'], 'name': r['name'], 'quantity': r['quantity'],
                    'status': r['status'], 'score': 1.0} for r in hits[:6]]
        return 'review', held[0] if len(held) == 1 else None, options, 'ambiguous_or_quantity'
    if not sale.cid and sale.buyer_name:
        name = normalized_name(sale.buyer_name)
        scored = sorted(
            ((SequenceMatcher(None, name, normalized_name(r.get('name'))).ratio(), r)
             for r in candidates if r['status'] == 'held' and normalized_name(r.get('name'))),
            key=lambda x: x[0], reverse=True,
        )
        near = [{'id': r['id'], 'name': r['name'], 'quantity': r['quantity'],
                 'status': r['status'], 'score': round(score, 3)}
                for score, r in scored[:5] if score >= 0.78]
        if near:
            return 'review', None, near, 'close_name'
    return 'unreserved', None, [], 'no_matching_reservation'
