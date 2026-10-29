"""Privacy-safe before/after data for committee writes (no request bodies)."""
import json
import re

FIELDS = {
    'events': ('title','slug','category','venue','starts_at','ends_at','doors_close_at',
               'capacity','hold_hours','published','industry','photo_short_name',
               'waitlist_enabled','availability_badge','sales_open_at',
               'reservations_close_at','union_sales_close_at','academic_year','target_years'),
    'ticket_types': ('name','price_pence','capacity','requires_purchase','members_only',
                     'max_per_user','sort_order','sales_open_at','sales_close_at'),
    'reservations': ('status','ticket_type_id','quantity','order_number','matched_sale_id',
                      'expires_at','confirmed_at','cancelled_at'),
    'admins': ('role','permissions'),
}
PATHS = (
    (r'/events/(\d+)(?:/(?:photo-short-name|waitlist-enabled|availability-badge))?', 'events'),
    (r'/ticket-types/(\d+)', 'ticket_types'),
    (r'/reservations/(\d+)/(?:cancel|order-number|committee-order-number|ticket-type|submit-order|confirm)', 'reservations'),
    (r'/admins/(\d+)', 'admins'),
)


def target_for(method, path):
    if method not in ('PUT','PATCH','POST','DELETE'):
        return None
    for regex, table in PATHS:
        match = re.fullmatch(regex, path)
        if match:
            return (table, int(match.group(1)))
    return None


def change_values(before, after):
    before, after = before or {}, after or {}
    keys = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    def safe(value):
        if isinstance(value, str) and len(value) > 400:
            return value[:400] + '...'
        if isinstance(value, list) and len(value) > 150:
            return value[:150]
        return value
    return ({key:safe(before.get(key)) for key in keys},
            {key:safe(after.get(key)) for key in keys})


async def snapshot(conn, target):
    table, row_id = target
    key = 'user_id' if table == 'admins' else 'id'
    # SQL identifiers come only from the fixed allowlists, never request text.
    value = await conn.fetchval(f'SELECT to_jsonb(t) FROM public.{table} t WHERE {key}=$1', row_id)
    if value is None:
        return None
    record = json.loads(value) if isinstance(value, str) else value
    values = {field:record[field] for field in FIELDS[table] if field in record}
    if table == 'events':
        rows = await conn.fetch('SELECT id,to_jsonb(t) AS fields FROM public.ticket_types t WHERE event_id=$1',row_id)
        for row in rows:
            item = json.loads(row['fields']) if isinstance(row['fields'], str) else row['fields']
            for field in FIELDS['ticket_types']:
                if field in item:
                    values[f'ticket #{row["id"]} · {field}'] = item[field]
    return values
