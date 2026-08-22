from pathlib import Path


SHARED_PRODUCTION_CONCURRENCY_WORKFLOWS = (
    ".github/workflows/drcloud-os-production.yml",
    ".github/workflows/drcloud-os-finance-amount-gap-proof.yml",
    ".github/workflows/drcloud-os-finance-exact-match-funnel-proof.yml",
    ".github/workflows/drcloud-os-finance-reconciliation-proof.yml",
    ".github/workflows/drcloud-os-qonto-local-source-proof.yml",
    ".github/workflows/drcloud-os-sumup-payout-recovery-proof.yml",
)


def test_shared_production_concurrency_keeps_all_pending_runs():
    """Shared production serialization must queue proofs instead of dropping pending runs."""
    for workflow in SHARED_PRODUCTION_CONCURRENCY_WORKFLOWS:
        text = Path(workflow).read_text(encoding="utf-8")
        assert "group: drcloud-os-production" in text, workflow
        assert "cancel-in-progress: false" in text, workflow
        assert "queue: max" in text, workflow
