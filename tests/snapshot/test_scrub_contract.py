"""Regressions for anonymised production snapshot safety across migrations."""
from pathlib import Path

SCRUB = (Path(__file__).resolve().parents[2] / 'deploy/snapshot/scrub.sql').read_text()


def test_opportunity_history_is_truncated_with_parent():
    assert "to_regclass('public.opportunity_publication_periods')" in SCRUB
    assert 'TRUNCATE TABLE public.opportunity_publication_periods, public.opportunities;' in SCRUB
    assert 'ELSE\n            TRUNCATE TABLE public.opportunities;' in SCRUB


def test_sales_import_customer_data_is_scrubbed_including_json():
    assert "to_regclass('public.product_sales_import_rows')" in SCRUB
    assert "'Imported Buyer ' || id" in SCRUB
    assert "'import' || id || '@staging.invalid'" in SCRUB
    assert "suggestions = '[]'::jsonb" in SCRUB
    assert "filename = 'union-import-' || id || '.csv'" in SCRUB


def test_arbitrary_pii_in_audit_json_is_not_copied():
    assert "to_regclass('public.action_audit')" in SCRUB
    assert 'TRUNCATE TABLE public.action_audit;' in SCRUB
