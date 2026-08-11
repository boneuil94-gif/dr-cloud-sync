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

## Update — Production Truth & Recovery Pack (2026-08-10)

| FEATURE | BEFORE | AFTER | LEVEL | EVIDENCE |
|---|---:|---:|---|---|
| OVH deploy exact commit | 66 | 68 | PRODUCTION_PROVEN (public scope) | SHA main/déployé/servi identique, health 200 et HTTPS datés |
| Backup / restore / rollback | 43 | 43 | CODE_EXISTS + TESTED; production NOT_PROVEN | aucune sauvegarde privée ni staging accessible |
| Data Hub truth contract | — | — | TESTED | fraîcheur séparée de couverture; production totals inconnus |
| Financial funnel | 57 | 57 | TESTED; production NOT_PROVEN | rapport fail-closed, aucune base production |

## Recovery Evidence Update — 2026-08-10

| FEATURE | CODED | WIRED | TESTED | PROD_DATA | PRODUCTION_PROVEN | EVIDENCE |
|---|---|---|---|---|---|---|
| Backup manifest/integrity | YES | YES | YES | NO | NO | SQLite backup API and local production-shaped bundle validated; no private production backup access |
| Isolated restore + app health | YES | YES | YES | NO | NO | real local copy, real DrCloud WSGI boot and `/health` 200; synthetic local data only |
| RPO/RTO measurement | YES | YES | YES | NO | NO | local observed RPO/RTO measured; production RPO remains UNKNOWN |
| Rollback N/N-1 | YES | PARTIAL | PARTIAL | NO | NO | staging/OVH-equivalent execution unavailable; `ROLLBACK_NOT_PROVEN` |
| SQLite WAL crash recovery | YES | YES | YES | NOT_APPLICABLE | NO | SIGKILL during uncommitted WAL write, reopen/integrity succeeded locally |

## Tentative Production Recovery Game Day — 2026-08-11

| Contrôle | Résultat | Niveau | Preuve / limite |
|---|---|---|---|
| Inventaire vrai backup | `PRODUCTION_BACKUP_MISSING` dans l'exécuteur | NOT_PROVEN | `/data/backups` absent/non monté; l'hôte OVH n'était pas accessible |
| Restore données production | `RESTORE_NOT_PROVEN` | NOT_EXECUTED | arrêt fail-closed, aucune substitution synthétique |
| RPO / RTO production | `UNKNOWN` / `UNKNOWN` | NOT_PROVEN | aucun backup sélectionnable |
| Rollback N/N-1 | `ROLLBACK_NOT_PROVEN` | NOT_EXECUTED | `OVH_EQUIVALENT_STAGING` indisponible |
| Compatibilité schéma / data loss | `UNKNOWN` / `NOT_RUN` | NOT_PROVEN | aucune migration ni écriture exécutée |
| Sécurité | SAFE MODE, aucune action externe | OBSERVED | aucune connexion provider, aucun paiement/publication/CRM |

Le rapport assaini `recovery_evidence_production.json` conserve les `null` au lieu d'inventer des compteurs. N est le merge PR #148 (`7ad7412…`) et N-1 son premier parent (`7bb7258…`), mais leur statut effectivement servi en production reste inconnu.

## GitHub Actions Production Recovery Game Day

Le workflow manuel `.github/workflows/drcloud-os-recovery-gameday.yml` déplace la preuve
sur l'hôte OVH accessible par l'environnement GitHub `production`. `restore-only` est le
mode par défaut; `full` demande également le rollback OVH-equivalent isolé. Il sélectionne
un vrai backup production `VALID` (ou lance d'abord le backup officiel), copie uniquement
son manifeste et sa base dans `mktemp`, vérifie checksum/taille/schéma/SQLite, puis démarre
l'image production sans secrets externes, en SAFE MODE, réseau interne et loopback.

L'artefact assaini de 30 jours expose des compteurs agrégés, RPO/RTO et les états
`PRODUCTION_BACKUP_VALID`, `PRODUCTION_DATA_PROVEN` et éventuellement `ROLLBACK_PROVEN`.
Un N-1 absent de l'historique known-good donne `ROLLBACK_NOT_PROVEN`. Le stockage actuel
reste classé `BACKUP_ON_HOST_ONLY`. **Cette PR n'est pas une exécution production** : les
scores restent Global strict **58**, Production maturity **49**, Deployment **68**.
