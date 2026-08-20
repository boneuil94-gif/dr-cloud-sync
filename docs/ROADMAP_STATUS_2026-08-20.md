# Roadmap status — 2026-08-20

This note refreshes stale roadmap wording using only production evidence already persisted after PR #185 and PR #186. It does **not** rescore any module.

## Scores unchanged

- strict global score: **58**
- Deployment: **85**

No numeric credit is added by this wording refresh.

## Recovery / RPO status

The old roadmap wording saying that business RPO is only LOW-confidence is stale.

Verified chain:
- PR #185 merged as `5b89052e125c0ef331f2348b8a292168aeadde54`;
- Production run `32308459103` succeeded on that exact SHA;
- fresh encrypted offsite backup run `32263186935`, attempt 5, succeeded; sanitized status artifact `9388376283` proves client-side Restic encryption, off-host object storage, remote check, and a fresh source backup;
- automatic remote-only recovery Game Day `32316883038` succeeded on the deployed SHA; evidence artifact `9388392020` proves offsite restore, integrity, application boot and health in safe isolated mode;
- measured RPO remained `0 s` with `MEDIUM` confidence;
- actual independent source coverage improved from **4 comparable / 6 unmeasurable** to **4 comparable / 4 unmeasurable**.

This is a coverage improvement, not proof of complete business-wide RPO. `HIGH` confidence is not claimed.

## Updated Deployment blocker wording

Replace the stale idea “RPO business de confiance LOW” with:

> Source-aware RPO is production-proven at MEDIUM confidence, but independent source coverage is still incomplete (4 comparable / 4 unmeasurable). External monitoring/alerting, SLOs, secret rotation, offsite retention policy, and periodic DR repetition remain unproven or incomplete.

Recommended next step:

> Continue reducing genuinely unmeasurable independent sources without inventing timestamps or derived-provider coverage; then address offsite retention, external monitoring/alerting/SLOs, secret rotation, and periodic Game Days.

## Priority refresh

The old priority “improve RPO measurement with `data_max_at` rather than `backup_created_at`” is complete as a measurement-method task. The remaining RPO priority is **coverage**.

Current green-path priorities:
1. reduce independent unmeasurable RPO sources only with truthful durable/provider timestamps;
2. classify `marketing_intelligence` as derived/recomputable only if architecture proves all persisted outputs can be regenerated from authoritative retained inputs;
3. keep `sumup_payouts.data_max_at = null` while the provider date lacks timezone;
4. add genuine SumUp merchant/readers direct-read jobs only if the existing official adapter contract supports those endpoints;
5. configure and prove offsite retention;
6. external monitoring, alerting and SLOs;
7. secret rotation and periodic DR;
8. finance end-to-end reconciliation, then stock/purchases, CRM/PII lifecycle, marketing/social providers, AI, and UX E2E/mobile.

## Guardrails

- no force-push or bypass of checks;
- no weakening of recovery/security evidence;
- no invented provider coverage, timestamps, scores or production proof;
- no credentials, tokens, raw provider payloads or PII in persisted evidence.
