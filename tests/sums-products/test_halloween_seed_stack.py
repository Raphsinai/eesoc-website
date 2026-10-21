"""The migration populates four real-world Halloween links, without overwrites.

Requires the compose test stack (with Union stub), as do other database tests.
"""
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / 'backend/database/migrations/084_halloween_shop_product_links.sql'


def test_halloween_2026_mapping_is_scoped_idempotent_and_respects_existing_links(compose_exec):
    def sql(query):
        return compose_exec('postgres', 'psql', '-U', 'eesoc', '-d', 'eesoc',
                            '-v', 'ON_ERROR_STOP=1', '-tA', '-c', query).strip()

    # Product-link migration normally runs on adapter startup; the test adds a
    # representative event afterwards, then executes the SQL again explicitly.
    assert sql("SELECT COUNT(*) FROM public.events WHERE slug = 'halloween'") == '0'
    event_id = int(sql("""
        INSERT INTO public.events (slug, title, starts_at, academic_year)
        VALUES ('halloween', 'Halloween x BioEng x BioSoc',
                '2026-10-31T18:00:00Z', '26-27') RETURNING id
    "").splitlines()[0])
    try:
        sql(f"""
            INSERT INTO public.ticket_types (event_id, name, price_pence, requires_purchase)
            VALUES
                ({event_id}, 'Early Bird - Alcoholic', 1750, TRUE),
                ({event_id}, 'Early Bird - Non-Alcoholic', 1250, TRUE),
                ({event_id}, 'General - Alcoholic', 2000, TRUE),
                ({event_id}, 'General - Non-Alcoholic', 1500, TRUE),
                ({event_id}, 'An unrelated ticket', 2000, TRUE)
        """)
        # An admin-selected product wins over the suggested migration mapping.
        sql(f"""
            INSERT INTO public.product_links
                (purpose, event_id, ticket_type_id, union_product_id, academic_year)
            SELECT 'event_ticket', {event_id}, t.id, 1999999, '26-27'
              FROM public.ticket_types t WHERE t.event_id = {event_id}
                AND t.name = 'General - Non-Alcoholic'
        """)
        for _ in range(2):
            sql(MIGRATION.read_text())
        rows = sql(f"""
            SELECT t.name || '|' || l.union_product_id
              FROM public.ticket_types t
              JOIN public.product_links l ON l.ticket_type_id = t.id
             WHERE t.event_id = {event_id} ORDER BY t.name
        """).splitlines()
        assert rows == [
            'Early Bird - Alcoholic|1003023',
            'Early Bird - Non-Alcoholic|1003043',
            'General - Alcoholic|1003049',
            'General - Non-Alcoholic|1999999',
        ]
    finally:
        sql(f"DELETE FROM public.events WHERE id = {event_id}")
