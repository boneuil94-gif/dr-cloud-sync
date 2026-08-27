from pathlib import Path


ROOT = Path(__file__).parents[1]
INVENTORY_WEB = (ROOT / "src/dr_cloud_sync/inventory_web.py").read_text(encoding="utf-8")


def test_sumup_merchant_and_reader_sources_have_real_direct_jobs():
    assert 'JobDefinition("sync_sumup_merchant","sumup_merchant","SUMUP_MERCHANT",sumup_interval)' in INVENTORY_WEB
    assert 'JobDefinition("sync_sumup_readers","sumup_readers","SUMUP_READERS",sumup_interval)' in INVENTORY_WEB
    assert '"SUMUP_MERCHANT":sumup_merchant' in INVENTORY_WEB
    assert '"SUMUP_READERS":sumup_readers' in INVENTORY_WEB


def test_sumup_direct_jobs_report_durable_counts_and_validated_merchant_business_time():
    start = INVENTORY_WEB.index("        def sumup_merchant(cursor):")
    end = INVENTORY_WEB.index("        intelligence=", start)
    direct_jobs = INVENTORY_WEB[start:end]
    assert "provider.merchant()" in direct_jobs
    assert "provider.readers()" in direct_jobs
    assert "import_merchant" in direct_jobs
    assert "import_readers" in direct_jobs
    assert direct_jobs.count('"records_available"') >= 2
    assert '"data_max_at":merchant["updated_at"]' in direct_jobs
    assert "imported_at" not in direct_jobs
    assert "created_at" not in direct_jobs
    assert "data_min_at" not in direct_jobs
