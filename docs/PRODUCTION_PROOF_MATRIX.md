# Production Proof Matrix — Audit V2

Méthode : 20 code, 20 wired, 20 tests, 25 production data, 10 observable, 5 documentation. `YES*` = preuve historique déclarée, non revalidée le 2026-08-10. `PARTIAL` ne vaut jamais preuve complète.

| FEATURE | CODED | WIRED | TESTED | PROD_DATA | OBSERVABLE | BLOCKED | SCORE | EVIDENCE |
|---|---|---|---|---|---|---|---:|---|
| Authentication / sessions / RBAC | YES | YES | YES | UNKNOWN | YES | NO | 78 | tests sécurité + route permission fail-closed; runtime public non testé |
| CI image | YES | YES | YES | NO | PARTIAL | NO | 76 | workflow PR/main construit et inspecte le SHA |
| OVH deploy exact commit | YES | YES | PARTIAL | UNKNOWN | PARTIAL | NO | 66 | workflow exact `head_sha`; serveur non interrogé |
| Backup / restore / rollback | YES | PARTIAL | PARTIAL | NO | NO | YES | 43 | scripts/runbook, aucun exercice daté fourni |
| Data Hub Sync All | YES | YES | YES | UNKNOWN | YES | NO | 70 | batches/retry/freshness/diagnostics; aucun batch prod fourni |
| ShopCaisse catalog write | YES | NO | YES | NO | PARTIAL | YES | 45 | dry-run explicite, aucun network writer |
| ShopCaisse sales/payments | YES | YES | YES | UNKNOWN | YES | NO | 64 | API/CSV provider + failures; aucune volumétrie prod |
| PrestaShop catalog | YES | YES | YES | PARTIAL | YES | NO | 72 | artifacts historiques + sync; fraîcheur/exhaustivité inconnues |
| PrestaShop sales | YES | YES | YES | UNKNOWN | YES | YES | 66 | paid states requis; configuration prod non prouvée |
| PrestaShop media | YES | PARTIAL | YES | UNKNOWN | PARTIAL | NO | 54 | import/checksum testés, sync prod non prouvée |
| SumUp transactions | YES | YES | YES | UNKNOWN | YES | YES | 64 | handler Sync All présent, credentials/rows inconnus |
| SumUp payouts | YES | YES | YES | UNKNOWN | YES | YES | 64 | handler + settlement présents, rows inconnus |
| SumUp fees/refunds/chargebacks | YES | NO | PARTIAL | NO | PARTIAL | YES | 38 | sources déclarées, aucun handler Sync All dédié |
| SumUp merchant/readers | PARTIAL | NO | PARTIAL | NO | PARTIAL | YES | 30 | endpoint/schema préparé, alimentation non démontrée |
| Qonto health/accounts | YES | YES | YES | YES* | YES | NO | 82 | CONNECTED/FRESH déclaré; snapshot absent |
| Qonto transactions | YES | YES | YES | YES* | YES | NO | 88 | 2 718 rows déclarées lors validation précédente |
| Qonto classification | YES | YES | YES | UNKNOWN | YES | NO | 67 | UI/API/ledger testés, coverage prod inconnue |
| Bank balance / ledger | YES | YES | YES | PARTIAL* | YES | NO | 76 | Qonto connecté historiquement; solde non revalidé |
| Sales Ledger | YES | YES | YES | UNKNOWN | YES | NO | 69 | sources et idempotence; couverture production inconnue |
| Finance Cockpit | YES | YES | YES | PARTIAL* | YES | YES | 61 | autorités identifiées; données coût/vente partielles |
| End-to-end settlement | YES | YES | YES | UNKNOWN | YES | YES | 57 | explorer/recompute; aucun décompte prod disponible |
| Stock ledger / projection | YES | YES | YES | UNKNOWN | YES | NO | 67 | movements/negative/incoming/coverage; terrain absent |
| Purchases / receipts / FIFO | YES | YES | YES | UNKNOWN | YES | NO | 63 | local workflow complet; fournisseurs live absents |
| Replenishment | YES | YES | YES | UNKNOWN | PARTIAL | YES | 57 | suggestions heuristiques, pas d'ordre externe |
| CRM Customer 360 / RFM | YES | YES | YES | UNKNOWN | YES | YES | 56 | coverage runtime; base prod absente |
| CRM consent | YES | YES | YES | UNKNOWN | YES | YES | 52 | evidence/UNKNOWN safe; provider de consent absent |
| Loyalty / reactivation | YES | PARTIAL | YES | NO | PARTIAL | YES | 38 | simulation explicite, aucun envoi externe |
| Marketing foundation/review | YES | YES | YES | UNKNOWN | YES | NO | 61 | pipeline interne et human review |
| Creative AI | YES | YES | YES | NO | PARTIAL | YES | 43 | générateur déterministe, aucun provider IA |
| Marketing publishing | YES | PARTIAL | YES | NO | YES | YES | 42 | queue/schedules, DisabledSocialProvider |
| Instagram official API | NO | NO | PARTIAL | NO | YES | YES | 18 | NOT_CONFIGURED |
| Facebook official API | NO | NO | PARTIAL | NO | YES | YES | 18 | NOT_CONFIGURED |
| TikTok official API | NO | NO | PARTIAL | NO | YES | YES | 18 | NOT_CONFIGURED |
| Snapchat official API | NO | NO | PARTIAL | NO | YES | YES | 18 | NOT_CONFIGURED |
| Analytics dashboards | YES | YES | YES | UNKNOWN | PARTIAL | YES | 51 | ledgers réels, coverage/validation E2E absents |
| Web/mobile UX | YES | YES | PARTIAL | UNKNOWN | PARTIAL | YES | 60 | assets/routes testés, aucun E2E navigateur/mobile |
