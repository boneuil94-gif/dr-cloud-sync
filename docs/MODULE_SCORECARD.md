# Module Scorecard — Audit V2

Notation stricte définie dans [AUDIT_V2.md](AUDIT_V2.md). Une absence de preuve est `UNKNOWN`, jamais assimilée à un succès ou à zéro donnée.

| Module | Score /100 | Justification / preuves | Blocages | Niveau suivant |
|---|---:|---|---|---|
| Architecture | 68 | domaines/repositories/adapters/jobs | composition WSGI 1 008 lignes, schémas dispersés | router/composition séparés, migration unique, graphe de dépendances |
| Core | 72 | lifecycle, SQLite, idempotence, retry, health | concurrence/crash non éprouvés | tests multi-worker/fault injection + metrics |
| Deployment | 66 | CI/CD exact SHA, Docker/Caddy, scripts DR | serveur/restore/rollback/monitoring non prouvés | exercice recovery et preuve publique datée |
| Security | 74 | auth, sessions, RBAC, CSRF, headers, audit | PII ops, CVE/pentest/rate-limit prod | SBOM/scans/pentest/revue RGPD |
| Data Hub | 65 | sources, batches, retry, freshness, schema health | freshness non égale exhaustivité | totaux autorité + alerting/SLO |
| ShopCaisse | 58 | API/CSV ventes/paiements testés | prod coverage inconnue; catalog write simulé | validation payload live et totaux |
| PrestaShop | 64 | catalog 72, sales 66, media 54 | paid states/live coverage/media | preuve par sous-module et reconciliation source |
| SumUp | 58 | transactions/payouts wired | autres endpoints préparés seulement | alimenter/mesurer chaque endpoint officiel |
| Qonto | 78 | client/ledger/2 718 rows historiques | fraîcheur et sous-modules non revalidés | evidence snapshot + coverage pagination/matching |
| Finance | 61 | cockpit/KPI issus des ledgers | coûts/settlements/closing partiels | réconciliation comptable et KPI contracts |
| Settlements | 57 | chaîne/modèles/explorer/review | aucun taux réel; statuts divergents | mesurer funnel complet sur prod |
| Stock | 67 | ledger/projection/receipts/value | terrain/mapping/convergence inconnus | stocktake et coverage source |
| Purchases | 63 | suppliers/PO/receipts/FIFO/replenishment | providers/budget prod absents | preuves d'usage et fournisseurs réels |
| CRM | 52 | 360/RFM/segments/consent | coverage N/D, loyalty simulée | identity/consent coverage + activation contrôlée |
| Marketing | 47 | foundation/review/cockpit internes | publication/learning production absents | provider officiel + attribution mesurée |
| Social | 22 | ports et garde-fous | 4 providers NOT_CONFIGURED | connecter API officielles et conformité |
| Analytics | 51 | agrégats sur ledgers, pas de fixtures UI identifiées | coverage et forecasts partiels | data contracts + validation dashboard |
| AI | 35 | génération déterministe revue | aucun provider/usage IA production | décision build/buy, coût/sécurité/evaluation |
| UX | 60 | navigation/pages/états/filtres | E2E/mobile/a11y absents, pages bloquées | parcours métier navigateur testés |
| Tests | 76 | 563 pass, domaines/API/migrations/security | 1 skip, live/load/DR/E2E absents | pyramide + production-like CI |
| Documentation | 70 | architecture/providers/deploy/runbooks riches | preuves d'exercice/SLO/incidents absentes | runbooks testés, ownership et evidence datée |

## Scores techniques séparés des modules

`ShopCaisse CODE 72 / PRODUCTION 38`; `PrestaShop Catalog 72 / Sales 66 / Media 54`; `Marketing INTERNAL_READY 61 / PROVIDER_READY 42 / PRODUCTION_READY 20`; chaque provider social officiel 18 et bloqué.

## Evidence update — 2026-08-10

Même méthode et aucun crédit pour le code seul. Deployment passe **66 → 68** grâce au SHA/health/HTTPS publics datés. Data Hub **65 → 65**, Finance **61 → 61**, Settlements **57 → 57**, Security **74 → 74**, Observability **61 → 61**. Le global reste **58 → 58**. Restore, rollback, backup inventory privé, coverage et funnel restent `NOT_PROVEN`.
