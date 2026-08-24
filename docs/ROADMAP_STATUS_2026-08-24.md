# Roadmap status — 2026-08-24

This note refreshes stale roadmap wording from the evidence persisted through 2026-08-24. It does **not** rescore any module and does not modify `config/roadmap_v3.json` numeric scores.

## Scores unchanged

- strict global score: **58**
- Deployment: **85**

No numeric credit is added by this evidence refresh. A score change still requires a separate reproducible Audit V2 rescore.

## Production / storage facts now proven

- current `main`: `e695284b871e730f094a6a8a0840df1f1ab9cdcc` after PR #226;
- PR #226 changed only the group-composition production proof workflow/tests, so the Production workflow skip on that workflow-only merge is expected and is not a production failure;
- SQLite multi-worker/concurrency is **TESTED**; the old wording “not proven” is stale, but this is not a new score claim;
- the latest scheduled encrypted offsite backup run `32690360110` succeeded on `main` and its sanitized artifact proves off-host client-side encryption, fresh source backup, `retention=RETENTION_APPLIED`, and `remote_check=PROVEN`;
- the automatic remote-only recovery Game Day `32690432892` succeeded on the same SHA with `OFFSITE_RESTORE_PROVEN`, `APP_BOOT_OK`, `HEALTH_OK`, observed restore time **15 s**, observed RPO **0 s**, RPO confidence **MEDIUM**, **4 comparable sources / 3 unmeasurable sources**.

Therefore the old blockers “offsite retention policy unproven” and “no periodic remote-only DR evidence” are stale. Retention 7/4/12 is **PRODUCTION_PROVEN** and at least one scheduled backup → automatic remote-only recovery cycle is now production-proven. This does not by itself establish long-term cadence/SLO compliance.

## Source-aware RPO wording

Replace stale wording that says independent coverage is 4 comparable / 4 unmeasurable with:

> Source-aware RPO is production-proven at MEDIUM confidence. The latest scheduled remote-only recovery evidence reports 4 comparable independent sources and 3 unmeasurable sources. Business-wide exhaustiveness is still not proven, so HIGH confidence is not claimed.

Remaining RPO work is coverage and authority, not inventing fallback timestamps.

## Finance progress

The old roadmap statements “funnel financier non mesuré” and “aucun taux production” are stale for the local SumUp→Qonto payout leg.

Production/local-ledger evidence now proves:

- Qonto local source is populated and fresh rather than empty;
- Qonto imported transaction status shape is known and `COMPLETED` is handled narrowly for Qonto without broadening other bank semantics;
- exact matching remains fail-closed on normalized reference + currency + amount, with no fuzzy/date/tolerance matching;
- exact multi-line SumUp payout grouping is allowed only for a complete same-reference + same-currency group whose exact Decimal sum equals one unique eligible Qonto credit;
- this raised local payout reconciliation to **773 matched / 793 payouts**, **20 unresolved**, **0 ambiguous** (about **97.48%** local coverage);
- the two remaining multi-line groups are not explained by exact group amount sums or aggregate payout fees;
- PR #225 adds sanitized composition classification for those remaining groups; PR #226 adds the production proof workflow, but that composition proof has **not yet run** because it awaits a compatible successful Production trigger.

Important guardrail: these are local-ledger facts. Provider exhaustiveness / provider-authority totals remain explicitly unproven and no score is increased.

## Monitoring / security / DR wording

External health alerting code exists, but roadmap completion must distinguish code from sustained operating evidence. Remaining work includes:

- SLI/SLO history and alert-delivery history over a meaningful operating window;
- secret rotation **evidence** (not merely secret presence in GitHub Actions);
- repeated periodic DR cadence over multiple scheduled cycles, even though one scheduled offsite backup → remote-only recovery cycle is now proven;
- source-aware RPO coverage for the remaining 3 unmeasurable independent sources;
- provider-authority / exhaustiveness proofs for production connectors.

## Priority refresh

Current green-path priorities, without score inflation:

1. Keep the pending group-composition proof opportunistic: inspect it only after a compatible successful Production trigger; do not block unrelated roadmap work waiting for it.
2. Build monitoring/SLO history and prove alert delivery without logging credentials, payload bodies or PII.
3. Record secret-rotation evidence safely; never persist secret values.
4. Accumulate periodic DR cadence evidence and keep source-aware RPO at truthful MEDIUM confidence until the remaining independent source gaps are resolved.
5. Prove production source exhaustiveness / authority totals for ShopCaisse, PrestaShop, SumUp and Qonto where the provider contract permits it.
6. Prove real stock/supplier usage and purchase-chain coverage; keep `PARTIAL` data fail-closed from approval/ordering.
7. Complete CRM consent/retention policy evidence; the local anonymisation lifecycle exists, but policy duration/coverage still requires explicit authority.
8. Add browser E2E/mobile/a11y coverage for core business journeys.
9. Configure official social providers only when approved and measurable; never present mocks/sandbox as production publication.
10. Keep AI blocked until a measured use case, privacy boundary, cost budget, evaluation and human-review contract exist.

## Closed wording blockers

The following roadmap phrases must no longer be used as current blockers:

- “SQLite concurrence/multi-worker non prouvée” → **TESTED**;
- “politique de rétention offsite non prouvée” → **PRODUCTION_PROVEN** on scheduled backup `32690360110`;
- “aucun DR remote-only périodique” → one scheduled backup-triggered remote-only recovery cycle is **PRODUCTION_PROVEN** via `32690432892`; cadence history still needs repetition;
- “4 comparable / 4 unmeasurable” → latest evidence is **4 / 3**, confidence **MEDIUM**;
- “funnel SumUp payout→Qonto non mesuré” → local exact payout reconciliation is measured at **773/793**, while provider exhaustiveness remains open.

## Guardrails

- no force-push or bypass of checks;
- no score increase without formal reproducible rescore;
- no weakening of recovery/security evidence;
- no invented provider coverage, timestamps, provider authority or production proof;
- no credentials, secret values, raw provider payloads, bank references or PII in persisted evidence;
- local-ledger evidence is never presented as provider exhaustiveness.
