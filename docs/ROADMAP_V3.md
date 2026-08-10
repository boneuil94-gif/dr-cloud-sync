# Roadmap V3 — fondée sur le Grand Audit V2

Cette roadmap ne crédite une étape qu'avec code + wiring + tests + observabilité + preuve de données production + documentation/runbook. Effort relatif : S (≤ quelques jours), M (ordre de la semaine), L (plusieurs semaines), à recalibrer par l'équipe.

## Priorités consolidées

| Prio | Problème / impact | Module | Solution vérifiable | Effort | Dépendances |
|---|---|---|---|---:|---|
| P0 | État secrets/HTTPS/backup production non vérifié : accès ou perte potentielle | Security/Deployment | audit opérateur immédiat, restaurer une copie isolée, joindre SHA/health/horodatage | M | accès OVH, coffre secrets |
| P1 | Pas de preuve de rollback/recovery/RPO-RTO | Deployment/Core | exercice game-day, rollback SHA et restore, chronométrage et rapport | M | sauvegarde cohérente, staging |
| P1 | Couverture sources et rapprochement inconnus : KPI possiblement trompeurs | Data/Finance | totaux d'autorité ShopCaisse/Presta/SumUp/Qonto et funnel MATCHED… | L | credentials read-only, définitions métier |
| P1 | SQLite concurrence/multi-worker non prouvée | Core | load/locking/crash tests, décision SQLite durci vs PostgreSQL | M | profil charge, stratégie migration |
| P1 | PII/consent lifecycle non prouvé | CRM/Security | rétention, erase/export, chiffrement/accès, consent coverage | M | DPO/métier |
| P1 | SumUp sous-endpoints apparaissent comme sources mais ne sync pas | SumUp/Data Hub | brancher ou marquer explicitement indisponibles fees/refunds/chargebacks/readers | M | API scopes officiels |
| P2 | Monolithe WSGI et schémas dispersés augmentent risque de changement | Architecture | extraction progressive router/composition/migrations, sans big bang | L | tests regression |
| P2 | Absence E2E/mobile/a11y/load | Tests/UX | parcours Playwright + axe + budgets perf/concurrence | M | environnement CI |
| P2 | CVE/SBOM/secret scan/pentest non prouvés | Security | jobs CI + revue et pentest | M | politique remédiation |
| P2 | Social/marketing peut paraître prêt sans provider | Marketing/Social | labels capability et blocage dur tant que sandbox/prod officiels absents | S | product wording |
| P3 | Multiples chaînes hydration/reconciliation | Architecture | tracer usages, ADR puis déprécier seulement avec métriques | M | telemetry routes/jobs |

## Phase 1 — Stabilisation critique

1. Fermer P0 : preuve datée commit servi, HTTPS/headers, secrets/compte bootstrap et restauration isolée.
2. Exercer rollback et recovery; publier RPO/RTO mesurés et monitoring/alertes health, jobs, disque, backup.
3. Établir contrats de vérité : total source, imported, rejected, duplicate, freshness, last cursor pour chaque connecteur; empêcher `FRESH` si coverage inconnue.
4. Tester contention/crash SQLite et choisir explicitement architecture mono-writer ou trajectoire PostgreSQL.

**Exit** : aucun P0, restores et rollback réussis, alerts testées, Data Hub ne survend aucun statut.

## Phase 2 — Complétude métier

1. Valider ShopCaisse ventes/paiements mixtes et PrestaShop catalog/sales/media avec échantillons et totaux d'autorité.
2. Rendre honnêtes SumUp merchant/transactions/payouts/fees/refunds/chargebacks/readers : branché et mesuré, ou `NOT_CONFIGURED` sans ambiguïté.
3. Mesurer la chaîne settlement complète et publier MATCHED/PROBABLE/AMBIGUOUS/UNMATCHED/anomalies; aligner le vocabulaire.
4. Mesurer Stock mapping/negative/value, Purchases receipt/FIFO/budget et CRM customer/revenue/consent coverage.

**Exit** : chaque KPI Finance possède autorité, freshness et coverage; taux de rapprochement reproductible.

## Phase 3 — Automatisation

1. Fiabiliser scheduler/retry/idempotence avec tests panne réseau, reprise, doublons, batch concurrent et dead-letter/replay observables.
2. Automatiser contrôles data quality, clôtures de périodes et alertes d'anomalies, toujours avec validation humaine pour mutation sensible.
3. Ajouter E2E navigateur/mobile/a11y et production-like load/restore aux gates CI.

**Exit** : automation rejouable, observable, sans double écriture et avec intervention opérateur documentée.

## Phase 4 — Intelligence

1. Ne retenir que deux cas mesurables : prévision réapprovisionnement et proposition marketing fondée sur vente/stock/marge avec baseline.
2. Décider si un provider IA apporte un gain; avant activation : minimisation des données, coût/budget, sécurité, évaluation, fallback, human review et audit.
3. Connecter des providers sociaux officiels uniquement si nécessaires au business; sandbox puis canary production, jamais simulation présentée comme publication.

**Exit** : uplift/erreur/coût mesurés contre baseline; aucune « IA » décorative.

## Phase 5 — SaaS-grade hardening

1. Multi-instance/storage selon résultats de charge, migrations backward-compatible, canary et rollback automatique.
2. SLO/SLI, on-call, incident drills, DR périodique, audit trail exportable, rétention/effacement et pentest indépendant.
3. Evidence matrix générée automatiquement depuis CI + télémétrie production, revue à chaque release.

**Exit** : niveau 90+ seulement après au moins un cycle d'exploitation mesuré; 95 après assurance indépendante et DR répété. 100 n'est pas une cible roadmap permanente.

## Top 10 prochaines actions

1. Restaurer la dernière sauvegarde dans un environnement isolé.
2. Capturer SHA servi, `/health`, HTTPS/headers et versions de schéma.
3. Exporter un snapshot Data Hub anonymisé avec freshness, cursors et totaux d'autorité.
4. Calculer le funnel SALE→PAYMENT→SUMUP→PAYOUT→QONTO.
5. Vérifier ShopCaisse multi/mixed payments sur payloads production anonymisés.
6. Prouver exhaustivité PrestaShop pagination + paid states et media coverage.
7. Corriger le statut des cinq sources SumUp non réellement alimentées.
8. Exécuter tests contention/crash/load SQLite et documenter la décision storage.
9. Ajouter E2E des parcours Data Hub, Finance, Stock, CRM et Marketing bloqué.
10. Lancer SBOM/CVE/secret scan puis exercice incident/rollback.
