# Audit V2 — Evidence update — 2026-08-19

## Source-aware RPO production proof

The remote-only recovery Game Day run `32263769005` succeeded on `e07d762d092e7c9bd9885bc32d963a54a4a76517`.

Proven facts:
- remote Restic snapshot available and integrity PASS;
- restore result `OFFSITE_RESTORE_PROVEN`;
- app boot `APP_BOOT_OK` and health `HEALTH_OK`;
- restore remained isolated: no local backup used for restore, no production volume mounted, no production port published, no provider auth, internal-only network;
- RPO method is now `live_vs_backup_source_watermarks`, not the old `backup_created_at` proxy;
- observed restore: 14 s;
- observed business data gap / RPO: 0 s;
- confidence: `MEDIUM`;
- 1 source comparable, 12 unmeasurable.

Interpretation: the source-aware RPO mechanism is production-proven and replaces the LOW-confidence time proxy. The business-wide RPO is **not** fully proven because source coverage is still incomplete. No HIGH confidence is claimed.

## Production data coverage and finance funnel proof

The read-only production data proof run `32270931772` succeeded on `f8ceebcdb1324ef66b2e918055919a496166c432`.

The production database passed `PRAGMA quick_check`; the proof opened it read-only, made no provider network call, used no external provider authentication, and performed no mutation.

Observed control-plane facts:
- 19 declared sources;
- 13 currently configured according to stored source state;
- authoritative provider coverage is not proven for any configured source because upstream authority totals are not yet captured;
- Qonto has 2,751 locally imported rows and a real `data_max_at`;
- ShopCaisse sales has 751 imported rows but no `data_max_at`;
- PrestaShop sales has 55 imported rows but no `data_max_at`;
- SumUp transactions has 11,105 imported rows but no `data_max_at`;
- SumUp payouts source is currently `ERROR`;
- several SumUp sub-sources (`merchant`, `fees`, `refunds`, `chargebacks`, `readers`) appear `CONNECTED` while having no successful sync timestamp and no imported rows. This is a truthfulness/wiring finding and must not be treated as production proof for those endpoints.

Observed local finance funnel counts:
- sales: 811;
- payments: 753;
- SumUp transactions: 11,105;
- SumUp payouts: 328;
- Qonto transactions: 2,751.

The proof deliberately reports `end_to_end_match_rate = null` and `end_to_end_status = NOT_PROVEN`. The local stage counts are real production facts, but they do not prove source exhaustiveness or SALE→PAYMENT→SUMUP→PAYOUT→QONTO reconciliation.

## Scoring decision

No global or module score is changed by this evidence update. The strict global score remains 58 and Deployment remains 85 until a formal reproducible re-score justifies numeric credit.

What changed is the factual backlog:
1. the old RPO proxy problem is replaced by source-aware production evidence; the remaining RPO problem is **coverage**, not measurement method;
2. source/funnel measurement has now been executed in production; the remaining problems are upstream authority totals, source timestamps and real reconciliation;
3. SumUp sub-source status/wiring truthfulness is now an explicit production finding.

Evidence files:
- `docs/evidence/source_aware_rpo_evidence_production_2026-08-19.json`
- `docs/evidence/production_data_evidence_2026-08-19.json`
