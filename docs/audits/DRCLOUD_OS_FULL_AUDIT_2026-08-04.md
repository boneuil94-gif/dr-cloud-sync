# Audit complet DrCloud OS — 4 août 2026

> **Nature :** audit documentaire, statique, tests locaux et contrôles HTTP non intrusifs. Aucun correctif, écriture de production, déploiement ou modification de roadmap.  
> **Révision auditée :** `ed4a29d134901918705f35ad3574a1068e8cab40` (`work`, merge PR #127).  
> **Échelle :** `OPERATIONAL`, `FUNCTIONAL_LOCAL`, `FOUNDATION_ONLY`, `PARTIAL`, `BLOCKED_EXTERNAL`, `BROKEN`, `NOT_IMPLEMENTED`, `UNKNOWN`.  
> **Preuves :** `CODE`, `TEST`, `PRODUCTION`, `DATABASE`, `WORKFLOW`, `DOCUMENTATION`, `INFERENCE`.

## 1. Executive Summary

DrCloud OS est un **monolithe Python/WSGI, SQLite et JavaScript sans framework**, étendu (115 noms de tables déclarés détectés, 40 pages/API métier, 514 tests collectés), effectivement déployé. La production répond, force HTTPS et l'authentification, sert exactement le commit audité et annonce une base accessible. Cela prouve le socle, **pas** les flux métier authentifiés ni la présence de données réelles.

La roadmap affiche **118/159 jalons `DONE`, soit 74,2 %**. L'audit estime la maturité livrée à **48/100** et la réalisation stricte de roadmap à **46 %** : beaucoup de jalons prouvent un contrat local, une UI ou un provider-neutral, mais pas une exploitation réelle. Les connecteurs PrestaShop, ShopCaisse, SumUp et Qonto existent; le workflow exige leurs secrets. Leur santé et leurs volumes de production restent `UNKNOWN` sans session Data Hub. Qonto a en outre un historique récent de blocage WAF/401. Meta, Instagram, TikTok et Snapchat sont explicitement enregistrés `NOT_CONFIGURED`; la publication est désactivée sans publisher réel.

Le signal contradictoire majeur est la suite complète locale : **475 réussites, 1 ignoré, 3 échecs et 35 erreurs** en 193,75 s. La majorité des erreurs découle d'une fuite de descripteurs SQLite (`Too many open files`), qui rend le résultat global `BROKEN` dans cet environnement Python 3.14 alors que CI cible Python 3.12. Les tests restent nombreux et utiles, mais ne prouvent pas les providers ni la production.

**Conclusion :** bon socle local et production vivante; intégrations, finance, settlements, marketing et CRM demeurent `PARTIAL` faute de preuve authentifiée des données et flux bout-en-bout. Seuls Core Application, Authentication et Deployment sont classés `OPERATIONAL` avec les preuves accessibles.

## 2. Score global

| Domaine | Score /100 | Poids | Justification (preuves) |
|---|---:|---:|---|
| Core | 72 | 12 % | Application servie, architecture modulaire, mais fuite de connexions sous suite complète. `CODE TEST PRODUCTION` |
| Data | 45 | 12 % | Schéma riche et contrôles prévus; base/agrégats de production inaccessibles. `CODE TEST PRODUCTION INFERENCE` |
| Integrations | 38 | 12 % | Quatre adapters réels, santé authentifiée non observée; sociaux absents. `CODE WORKFLOW DOCUMENTATION` |
| Finance | 43 | 10 % | Ledgers et vues testés, autorité et valeurs réelles non vérifiées. `CODE TEST` |
| Settlements | 47 | 9 % | Matching, preuves et revue présents; taux/volumes réels inconnus. `CODE TEST` |
| Marketing | 40 | 8 % | Cockpit et pipeline provider-neutral; aucun publisher réel configuré. `CODE TEST` |
| CRM | 36 | 7 % | Fondation très large récemment mergée, aucune donnée client réelle prouvée. `CODE TEST GIT` |
| Security | 66 | 8 % | Auth/RBAC/CSRF/cookies/headers solides; dépendances et production authentifiée non auditées. `CODE TEST PRODUCTION` |
| Operations | 51 | 7 % | Déploiement exact, web+worker et sauvegarde; rollback/worker vivant non observés. `WORKFLOW PRODUCTION CODE` |
| UX | 45 | 5 % | Pages nombreuses, mais revue visuelle authentifiée/mobile impossible. `CODE TEST INFERENCE` |
| Testing | 58 | 6 % | 514 cas, mais suite rouge et providers simulés. `TEST` |
| Documentation | 62 | 4 % | Documentation abondante, affirmations souvent au-delà de la preuve opérationnelle. `DOCUMENTATION CODE` |

**Score pondéré : 48/100** (arrondi; poids orientés risque : Core/Data/Integrations 36 %, Finance/Settlements 19 %, reste 45 %). Une indisponibilité de preuve n'est pas transformée en succès ni automatiquement en panne.

## 3. Tableau global des modules

| Module | Statut réel | Code | Tests | Production | Données réelles | Blocage principal | Priorité |
|---|---|---:|---:|---:|---:|---|---|
| Core Application | OPERATIONAL | oui | oui, suite rouge | oui | n/a | fuite FD en tests | P0 |
| Authentication | OPERATIONAL | oui | oui | redirection/login | n/a | session non fournie | P1 |
| RBAC | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | n/a | rôles prod non observés | P1 |
| AuditLog | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | rétention/export non prouvés | P1 |
| Administration | PARTIAL | oui | oui | route protégée | UNKNOWN | contenu authentifié inaccessible | P1 |
| Dashboard | PARTIAL | oui | oui | route protégée | UNKNOWN | KPIs réels inconnus | P1 |
| Data Hub | PARTIAL | oui | oui | route protégée | UNKNOWN | session/agrégats absents | P0 |
| Backup | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | restauration prod non démontrée | P1 |
| Deployment | OPERATIONAL | oui | oui | commit exact | oui (DB ok seulement) | rollback non automatisé | P1 |
| Product Catalog | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | catalogue prod inaccessible | P1 |
| Product Media | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | suite fuit les FD | P1 |
| PrestaShop Catalog | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | health/sync prod inconnus | P0 |
| PrestaShop Sales | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | états payés et backfill | P0 |
| ShopCaisse Sales | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | API/flux terrain non observés | P0 |
| ShopCaisse Payments | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | historique exhaustif inconnu | P0 |
| Local Stock | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | validation terrain | P1 |
| Purchases | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | fournisseurs/factures réels | P1 |
| Suppliers | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | provider documents absent | P2 |
| SumUp Transactions | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | health/volume prod | P0 |
| SumUp Payouts | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | couverture/backfill | P0 |
| SumUp Fees | FOUNDATION_ONLY | oui | oui | UNKNOWN | UNKNOWN | ingestion réelle non prouvée | P1 |
| SumUp Refunds | FOUNDATION_ONLY | oui | oui | UNKNOWN | UNKNOWN | ingestion réelle non prouvée | P1 |
| SumUp Chargebacks | FOUNDATION_ONLY | oui | oui | UNKNOWN | UNKNOWN | ingestion réelle non prouvée | P1 |
| Qonto Bank | BLOCKED_EXTERNAL | oui | oui | UNKNOWN | UNKNOWN | antécédents WAF/401, santé inconnue | P0 |
| Bank Ledger | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | Qonto réel | P0 |
| Sales Ledger | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | autorité CA prod non validée | P0 |
| Finance | PARTIAL | oui | oui | route protégée | UNKNOWN | sources/règles réelles | P0 |
| Settlement Ledger | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | sources réelles | P0 |
| Settlement Cockpit | PARTIAL | oui | oui | route protégée | UNKNOWN | métriques réelles | P0 |
| Settlement Explorer | PARTIAL | oui | oui | route protégée | UNKNOWN | volumes réels | P1 |
| Marketing Foundation | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | provider-neutral | P1 |
| Social Analytics | FOUNDATION_ONLY | oui | oui | UNKNOWN | non | sources NOT_CONFIGURED | P1 |
| Marketing Operations | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | cockpit sans providers | P1 |
| Social Providers | NOT_IMPLEMENTED | ports seuls | contractuels | non vérifié | non | adapters/credentials absents | P1 |
| Social Publishing | BLOCKED_EXTERNAL | pipeline | oui, doubles | UNKNOWN | non | publishers désactivés | P1 |
| CRM | FOUNDATION_ONLY | oui | oui | route protégée | UNKNOWN | aucune ingestion réelle prouvée | P1 |
| Loyalty | FOUNDATION_ONLY | oui | oui | UNKNOWN | UNKNOWN | règles/opérations réelles | P2 |
| Automation Worker | PARTIAL | oui | oui | UNKNOWN | UNKNOWN | heartbeat/runs inaccessibles | P0 |
| Notifications | FOUNDATION_ONLY | agrégées UI | peu | UNKNOWN | UNKNOWN | canal sortant absent | P2 |
| Exports | FUNCTIONAL_LOCAL | oui | oui | UNKNOWN | UNKNOWN | contrôle PII prod inconnu | P1 |
| Roadmap | BROKEN | oui | oui | route protégée | n/a | 74,2 % affiché vs 46 % audité | P1 |

## 4. Production réelle

Contrôles non intrusifs le 2026-08-04 entre 13:57:49 et 13:57:55 UTC :

* `http://osdrcloud.fr/` → `308` HTTPS; `/` → `303 /login`; `/login` → `200` (1 027 octets). `PRODUCTION`
* `/health` → `200` en 0,884 s, `{status: ok, application: drcloud-os, version: 1.0.0, commit: ed4a29d…, build_date: 2026-08-04T13:50:38Z, database: ok}`. `PRODUCTION`
* `/roadmap`, `/administration`, `/finance`, `/settlements`, `/marketing`, `/crm`, `/api/data-hub` → `303 /login` (0,16–0,78 s). Cela prouve la protection, pas le fonctionnement. `PRODUCTION`
* CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` et `X-Frame-Options` observés. HSTS n'a pas été observé dans les réponses capturées. `PRODUCTION`
* Sans credentials/session : Data Hub, contenu des pages, responsive, console JS, erreurs runtime et données réelles = `UNKNOWN`. Aucun test intrusif effectué.

## 5. Connecteurs et Data Hub

| Connecteur | Auth configurée | Health réel | Sync réelle | Pagination | Backfill | Données production | État honnête |
|---|---:|---:|---:|---:|---:|---:|---|
| PrestaShop | workflow l'exige | UNKNOWN | code oui | oui | partiel | UNKNOWN | PARTIAL |
| ShopCaisse | workflow l'exige | UNKNOWN | code oui | adapter oui | import fichiers/API partiel | UNKNOWN | PARTIAL |
| SumUp | workflow l'exige | UNKNOWN | code oui | oui | transactions/payouts | UNKNOWN | PARTIAL |
| Qonto | workflow l'exige | UNKNOWN | code oui | oui | transactions | UNKNOWN | BLOCKED_EXTERNAL |
| Meta | non démontrée | non | non | non | non | non | NOT_IMPLEMENTED |
| Instagram | non démontrée | non | non | non | non | non | NOT_IMPLEMENTED |
| TikTok | non démontrée | non | non | non | non | non | NOT_IMPLEMENTED |
| Snapchat | non démontrée | non | non | non | non | non | NOT_IMPLEMENTED |

Le Data Hub persiste `status`, tentative/réussite/erreur, curseur, lignes, durée, historique et planification. La fraîcheur n'est `FRESH` qu'après `last_success_at`, bon garde-fou contre un faux vert. Cependant un health réussi pose `CONNECTED` sans import; il faut donc toujours lire fraîcheur et `rows_imported`. `CODE`

Sources enregistrées : ShopCaisse sales, PrestaShop sales/catalog, Qonto bank, achats/stock locaux, SumUp transactions/payouts, supplier documents, quatre réseaux sociaux et marketing intelligence. Les sociaux et supplier documents démarrent `NOT_CONFIGURED`; Qonto/ShopCaisse configurés mais non encore lus démarrent `UNAVAILABLE`, non `CONNECTED`. `CODE`

**Champs production demandés (status, freshness, succès/erreur, volume, durée, historique, bouton test, curseur, période) : `UNKNOWN`**, car l'API est authentifiée et aucune session n'a été fournie. Le code expose les champs sauf une période couverte normalisée; les tests de boutons utilisent providers réels seulement s'ils sont injectés. `CODE PRODUCTION`

## 6. Données, migrations et autorités métier

Aucune base SQLite n'est versionnée dans le dépôt. La base production n'est pas téléchargeable et `/health` ne publie volontairement aucun agrégat. Donc taille, tables physiques, migrations appliquées, `user_version`, volumes, min/max, doublons, nulls, orphelins et statuts de production sont `UNKNOWN`. `DATABASE PRODUCTION`

L'analyse statique trouve **115 noms de tables déclarés** (hors faux positif SQL dynamique), avec migrations additives dispersées dans les repositories; SumUp a une migration nommée `sumup_reversals_and_settlement_coverage_20260803` et positionne `PRAGMA user_version`. Il n'existe pas de répertoire de migrations global ordonné : risque de dérive entre constructeurs. `CODE`

Les `PRAGMA integrity_check` et `foreign_key_check` ont été exécutés sur les SQLite créées par les tests au fil de la suite, mais aucune base durable n'est restée dans le dépôt; ils ne peuvent donc représenter la production. Le seul résultat production disponible est `database: ok`, simple requête de disponibilité. `TEST PRODUCTION`

| Donnée | Autorité attendue dans le code | Risque contradictoire |
|---|---|---|
| CA / ventes | `sale_events` / Sales Ledger | coexistence `sales`, `sale_lines`, imports PrestaShop et ShopCaisse; déduplication cross-source non prouvée |
| paiements | `sale_payments`, puis Settlement Ledger | SumUp transactions peut être compté comme vente si agrégateur mal choisi |
| stock | `stock_movements` projeté | observations ShopCaisse/externes sont des observations, pas l'autorité; UI doit le rendre clair |
| produits/prix | `drcloud_products`, PrestaShop observation | tables catalogue historiques et snapshots peuvent diverger |
| achats/coûts/marges | purchase cost ledger + lots FIFO | couverture incomplète ne doit pas devenir zéro |
| soldes/transactions bancaires | `bank_balances` / `bank_transactions` Qonto | Qonto indisponible rend la trésorerie inconnue, pas nulle |
| clients | `crm_customers` + identities | ingestion multi-source et consentements réels non prouvés |
| social/campagnes | snapshots + marketing campaigns | fixtures/provider-neutral ne constituent pas une mesure externe |

## 7. Finance

Les services exposent summary, cashflow, tax, profitability, transactions et rapprochements. Sales Ledger est append-only/idempotent; Bank Ledger, frais, remboursements, chargebacks, payouts, coûts FIFO et allocations existent. `CODE TEST`

Risques : double comptage PrestaShop/ShopCaisse, dates commande/paiement/payout/banque différentes, TVA et marge présentées malgré coûts inconnus, zéros de fallback, remboursements/chargebacks incomplets. Aucun montant réel n'est publié ici. `INFERENCE`

Flux audité :

```text
ShopCaisse [PARTIAL] ─┐
                      ├→ Sales Ledger [FUNCTIONAL_LOCAL]
PrestaShop [PARTIAL] ─┘           ↓
                            SumUp transactions [PARTIAL]
                                   ↓
                            SumUp payouts [PARTIAL]
                                   ↓
                            Qonto [BLOCKED]
                                   ↓
                            Finance [PARTIAL]
```

## 8. Settlements

Le code possède ledger, preuves, liens, matching, cockpit, explorer, anomalies, détails/timeline, notes, confirmation/rejet/détachement/revue, backfill et recompute. `CODE TEST`

Les vrais volumes `MATCHED`, `POSSIBLE`, `UNMATCHED`, `CONFLICT`, `NOT_EVALUATED`, taux de rapprochement et argent en transit sont **UNKNOWN** sans accès authentifié. L'absence de SumUp/Qonto ne doit pas être comptée comme anomalie; elle doit être une couverture source indisponible. La roadmap `DONE` sur cockpit/explorer décrit donc au mieux `FUNCTIONAL_LOCAL/PARTIAL`. `PRODUCTION INFERENCE`

## 9. Marketing

| Capacité | Audit | Motif |
|---|---|---|
| Marketing Foundation | FUNCTIONAL_LOCAL | modèles, repository, UI et tests |
| Creative AI | FOUNDATION_ONLY | génération abstraite/testée, aucun service externe attesté |
| Human Review | FUNCTIONAL_LOCAL | transitions et UI |
| Publishing Pipeline | FUNCTIONAL_LOCAL | file/contrôles/idempotence locaux |
| Social Analytics | FOUNDATION_ONLY | snapshots/ports; sources runtime `NOT_CONFIGURED` |
| Stock-driven Marketing | FUNCTIONAL_LOCAL | règles locales; données prod inconnues |
| Margin Intelligence | FUNCTIONAL_LOCAL | dépend de Finance/coûts inconnus |
| Learning Loop | FOUNDATION_ONLY | calculs présents; outcomes externes non prouvés |
| Operations Cockpit | FUNCTIONAL_LOCAL | read model provider-neutral |
| Providers sociaux | NOT_IMPLEMENTED | `DisabledSocialProvider`, aucun adapter concret injecté |
| Publication réelle | BLOCKED_EXTERNAL | `external_publication_enabled: false`, `PROVIDER_DISABLED` |

Les jalons marqués `DONE` ne doivent pas être compris comme live : l'UI annonce explicitement provider absent et les tests emploient des doubles. `CODE TEST DOCUMENTATION`

## 10. CRM

Présents : modèles/migrations pour customers, identities, références externes, adresses, activités/interactions, ventes liées, fusion, consentements, tags, segments/memberships, métriques, recommandations, fidélité, campagnes; API cockpit/liste/Customer 360; UI et tests. `CODE TEST GIT`

Non prouvés : ingestion active de clients PrestaShop/ShopCaisse, qualité d'identité, données/consentements réels, calcul RFM planifié, programme fidélité utilisé, envoi campagne, volumes production. Statut : **FOUNDATION_ONLY**, jamais `OPERATIONAL` sans données clients réelles. `PRODUCTION INFERENCE`

## 11. Frontend / UX — 20 constats prioritaires

Revue statique et routes publiques uniquement; pas de capture authentifiée, donc les constats visuels sont à confirmer.

1. **P0** Les KPIs peuvent paraître valides quand les sources sont indisponibles : afficher « indisponible », jamais 0.
2. **P0** Finance ne rend pas forcément la couverture source/coût indissociable des montants.
3. **P0** Settlements doit séparer absence de source et anomalie métier.
4. **P0** `CONNECTED` (health) peut être lu comme « données synchronisées » malgré 0 import.
5. **P1** Navigation très large (20+ destinations) et charge cognitive élevée.
6. **P1** Administration/Data Hub concentrent de nombreux contrôles techniques.
7. **P1** Tables longues : pagination cohérente non générale.
8. **P1** Filtres/état URL non uniformes entre pages.
9. **P1** Responsive authentifié non couvert par test navigateur réel.
10. **P1** Accessibilité clavier/lecteur d'écran non testée automatiquement.
11. **P1** États loading/timeout/offline non uniformes dans les JS.
12. **P1** Messages provider/diagnostics trop techniques pour un opérateur.
13. **P1** Actions sensibles nombreuses sans preuve de confirmation cohérente.
14. **P1** CRM Customer 360 risque une page excessivement longue.
15. **P1** Marketing duplique plusieurs routes dans un même template.
16. **P2** Toasts et erreurs ne suivent pas un composant commun.
17. **P2** États vides devraient expliquer source, dernière tentative et action.
18. **P2** Valeurs null/NaN/undefined ne font pas l'objet d'un test navigateur global.
19. **P2** Densité des cockpits Finance/Settlements/Marketing à tester sur mobile.
20. **P2** Service worker/PWA est testé contractuellement, pas par parcours offline réel.

## 12. Sécurité

**Forces :** PBKDF2, mot de passe ≥14 caractères, sessions persistées et révocables, cookie HttpOnly/SameSite/Secure en production, HMAC, CSRF sur écritures, RBAC par route, CSP/frame denial/nosniff, audit redacted, références de secrets, safe mode, utilisateur Docker non-root, médias contrôlés et tests traversal/spoofing. `CODE TEST PRODUCTION`

**Limites/risques :** pas de HSTS observé; pas de MFA; rate-limit en mémoire donc non partagé/redémarrable; CSP autorise style inline; secrets workflow seulement vérifiés non vides; exports CRM/finance peuvent contenir PII; dépendances non scannées dans CI; aucune preuve de rotation/rétention AuditLog; trust proxy dépend de configuration. Aucun secret n'a été imprimé. `CODE WORKFLOW PRODUCTION`

Les tests sécurité existants sont inclus dans la suite; leurs cas passés avant épuisement FD, mais l'exécution globale reste rouge. Recherche heuristique : seulement noms/placeholders/configuration attendue; aucune valeur credential manifeste confirmée. `TEST`

## 13. Déploiement

CI sur PR et push `main` : Python 3.12, installation, pytest, compileall, JS, shell/compose, diff-check, build Docker. Production ne part que du `workflow_run` réussi, push main, commit exact, environnement `production`; les cinq credentials principaux sont fail-closed. `WORKFLOW`

Le déploiement installe les secrets par SSH puis appelle `update.sh`. Compose comporte web et automation worker, volume SQLite partagé, healthcheck, Caddy et scripts backup/restore. La production sert le SHA exact. `WORKFLOW PRODUCTION CODE`

Écarts : CI ne teste pas de provider réel; un déploiement vert peut laisser Qonto bloqué WAF, réseaux sociaux absents, jobs `BLOCKED` ou données vides. Le workflow ne valide après déploiement que ce qu'implémente `update.sh`; rollback n'est pas visible au niveau workflow; santé du worker non publique; migrations sont distribuées au démarrage. `WORKFLOW INFERENCE`

## 14. Automation Worker

| Job | Intervalle défaut | Dépendances | Handler | Risque |
|---|---:|---|---:|---|
| sync_shopcaisse_sales | 600 s | — | oui | provider/curseur |
| sync_prestashop_sales/catalog | 900 s | — | oui | pagination/backfill |
| sync_bank_transactions | 900 s | — | oui | Qonto WAF |
| sync_sumup_transactions/payouts | configurable | transactions avant payouts | oui | couverture |
| sync_payment_settlements | idem SumUp | ShopCaisse + SumUp | oui | chaîne bloquée |
| refresh_sales_metrics | 300 s | PrestaShop sales | oui | CA incomplet |
| reconcile_bank_sales / refresh_finance | 300 s | bank puis reconcile | oui | dépendance bloquée |
| refresh_dashboard | 300 s | — | oui | faux zéro |
| refresh_marketing_signals | 300 s | sales metrics | oui | données partielles |
| social analytics ×4 | 21 600 s | — | handler générique | providers absents |
| marketing intelligence ×10 | 3 600–86 400 s | variables | oui | doublon STOCK_MARKETING/MEASURE_MARKETING |

Le worker heartbeat, les leases expirables, retry/max attempts, idempotence métier et historique sont présents. Deux paires partagent le même `job_type` (`STOCK_MARKETING`, `MEASURE_MARKETING`) : intention possible, mais observabilité ambiguë. Le curseur est mis à jour après retour de l'opération dans une transaction Data Hub; l'atomicité avec le ledger métier n'est pas garantie. Derniers runs/volumes/heartbeat production : `UNKNOWN`. `CODE PRODUCTION`

## 15. Tests

* Collectés/exécutés : **514**; **475 passed, 1 skipped, 3 failed, 35 errors**, 193,75 s, Python 3.14.4. `TEST`
* Cause dominante : `OSError: [Errno 24] Too many open files`; elle contamine roadmap, media, purchasing et hydration. Premier échec applicatif visible : ouverture SQLite impossible durant le test de dégradation PrestaShop. `TEST`
* Bien couverts : auth/CSRF/RBAC, catalog/inventory, ledgers, connectors contractuels, marketing, CRM, deployment contracts.
* Faiblement prouvés : providers live, navigateur E2E, données prod, restauration désastre réelle, charge/concurrence, upgrade complet de toutes versions.
* Couverture de lignes : non configurée/disponible. Tests temporels utilisent parfois horloges injectées, mais worker réel et timezone restent peu exercés. Fixtures synthétiques (notamment 478 produits) ne prouvent pas le terrain.

**20 tests manquants prioritaires :** (1) smoke prod authentifié read-only; (2) Data Hub vérité status/freshness/rows; (3) fuite FD suite complète; (4) PrestaShop pagination/backfill réel sandbox; (5) ShopCaisse pagination/reprise; (6) SumUp refunds/fees/chargebacks sandbox; (7) Qonto WAF/401 circuit; (8) CA cross-source anti-double-compte; (9) faux zéro finance; (10) timezone commande→payout→banque; (11) settlement absence-source; (12) backfill reconciliation historique; (13) migration copie prod anonymisée; (14) restore backup bout-en-bout; (15) worker/web même DB et heartbeat; (16) crash avant/après cursor commit; (17) Playwright desktop/mobile; (18) axe accessibilité; (19) exports PII/RBAC/formula injection; (20) social publication sandbox/idempotence.

## 16. Documentation

**À jour/alignés :** architecture générale, CI/CD, sécurité, Data Hub, Qonto/SumUp, settlements décrivent largement le code. **Partiels :** backup, runtime worker, finance et CRM ne donnent pas d'état de production vérifiable. **Contradictoires :** roadmap emploie `DONE` pour des fondations/provider-neutral; marketing/social suggère une complétude fonctionnelle sans providers; Qonto « réel » ne signifie pas health opérationnel. `DOCUMENTATION CODE`

Variables centrales observées dans workflow/env : PrestaShop URL/key/paid states, ShopCaisse key, SumUp key/merchant, Qonto credential, secrets OS, data/backup dirs, safe mode, trust proxy et intervalles. Une matrice exhaustive doc↔compose↔workflow manque. Fonctionnalités existantes sous-documentées : fuite potentielle de connexions, sémantique précise des zéros inconnus, atomicité curseur/ledger et authority map.

## 17. Roadmap réelle

| Module roadmap | Affiché | Audité | Justification |
|---|---:|---:|---|
| Core + architecture | 77 % | 65 % | prod réelle, mais notifications/permissions/config et suite rouge |
| Catalogue + mapping | 90 % | 68 % | local riche, catalogue prod non vu |
| Inventaire + EAN | 80 % | 58 % | tests, pas terrain/live |
| Stock + synchronisation | 90 % | 62 % | ledger local, sync réelle inconnue |
| Achats + fournisseurs | 100 % | 55 % | workflow local, aucune exploitation prod prouvée |
| Ventes | 77 % | 52 % | ledgers, connecteurs/backfill inconnus |
| Finance + pilotage | 82 % | 43 % | dépend des sources et coûts réels |
| Clients + fidélité | 60 % | 30 % | fondation récente sans ingestion réelle |
| Marketing + réseaux | 79 % | 34 % | provider-neutral, providers absents |
| Automatisations + IA | 0 % | 22 % | worker/jobs existent malgré roadmap à zéro |
| Dashboard | 45 % | 40 % | UI/API, KPIs prod inconnus |
| Sécurité + utilisateurs | 100 % | 68 % | bon local, opérations sécurité incomplètes |
| Production + PWA | 79 % | 67 % | commit exact et HTTPS; worker/UX non observés |
| **Total pondéré/estimé** | **74,2 %** | **46 %** | règle stricte code+test+deploy+données |

La correction recommandée ultérieure est de séparer `FOUNDATION`, `LOCAL_VERIFIED`, `DEPLOYED`, `LIVE_DATA` et `OPERATIONAL`, sans modifier le JSON dans cette PR d'audit.

## 18. Dette technique

| Priorité | Catégorie | Dette |
|---|---|---|
| P0 | code/data | connexions SQLite non libérées révélées par la suite complète |
| P0 | data | absence d'audit agrégé read-only de la base production |
| P0 | métier | autorité CA et anti-double-compte non démontrés |
| P0 | observabilité | worker/connecteurs/coverage non visibles sans session |
| P1 | migrations | migrations distribuées, pas de registre global |
| P1 | architecture | monolithe routeur WSGI très volumineux |
| P1 | finance | inconnu et zéro pas uniformément typés |
| P1 | sécurité | pas de MFA/HSTS observé/scans dépendances CI |
| P1 | tests | absence E2E navigateur et providers sandbox |
| P1 | docs | roadmap confond livraison locale et opérationnel |
| P2 | UI | composants/erreurs/pagination non uniformes |
| P2 | déploiement | rollback et smoke métier non explicites dans workflow |
| P2 | observabilité | job types dupliqués et logs structurés limités |
| P3 | confort | rationaliser documentation redondante |

## 19. Quinze risques principaux

| # | Risque | Prob. | Impact | Preuve | Mitigation |
|---:|---|---|---|---|---|
| 1 | épuisement descripteurs SQLite | haute | critique | suite rouge | fermer/partager connexions, test FD |
| 2 | double comptage CA | moyenne | critique | autorités multiples | règle source + invariant |
| 3 | faux zéro financier | moyenne | critique | sources inconnues | type `available/value/coverage` |
| 4 | Qonto bloqué WAF/401 | haute | élevée | historique PR | health/alerte/escalade provider |
| 5 | historique non réconcilié | haute | élevée | volumes inconnus | backfill borné + coverage |
| 6 | `CONNECTED` sans import | moyenne | élevée | sémantique health | badge séparé health/data |
| 7 | providers sociaux absents malgré DONE | certaine | moyenne | Disabled provider | déclasser roadmap |
| 8 | CRM sans données/consentement réel | haute | élevée | aucune preuve prod | pilote ingestion + DPIA |
| 9 | worker mort silencieusement | moyenne | élevée | heartbeat inaccessible | alerte publique interne |
| 10 | migration partielle/dérive | moyenne | élevée | migrations dispersées | registre global/startup gate |
| 11 | secret non valide mais non vide | moyenne | élevée | workflow `test -n` | health post-deploy |
| 12 | export PII excessif | moyenne | élevée | CRM/export permissions | minimisation/journalisation |
| 13 | cursor/ledger non atomiques | moyenne | élevée | DB transactions séparées | transaction/outbox |
| 14 | backup non restaurable | faible-moyenne | critique | test local seulement | restore drill périodique |
| 15 | CI verte différente du runtime | moyenne | moyenne | CI 3.12/audit 3.14 | matrice et pin runtime |

## 20. Quick wins

| Ordre | Délai | Action (à faire hors PR audit) | Impact | Risque | Module |
|---:|---|---|---|---|---|
| 1 | <2 h | exposer dans Data Hub `health ≠ synced`, coverage et `N/A` | fort | faible | Data |
| 2 | <2 h | alerte heartbeat worker dépassé | fort | faible | Ops |
| 3 | <2 h | documenter providers sociaux disabled | moyen | faible | Marketing |
| 4 | <1 j | test qui compte les FD avant/après suite ciblée | fort | faible | Core |
| 5 | <1 j | matrice d'autorité métier versionnée | fort | faible | Finance |
| 6 | <1 j | smoke post-deploy read-only des connecteurs | fort | moyen | Deploy |
| 7 | <1 sem. | rapport DB agrégé anonymisé (integrity/FK/volumes/coverage) | très fort | moyen | Data |
| 8 | <1 sem. | invariant anti-double-compte ventes | très fort | moyen | Sales |
| 9 | <1 sem. | drill backup/restore automatisé | très fort | moyen | Ops |
| 10 | <1 sem. | E2E Playwright desktop/mobile/auth | fort | faible | UX |

## 21. Plan d'action 7/30/90 jours

### 7 jours — stabiliser
1. Diagnostiquer/corriger la fuite SQLite et rendre `pytest -q` vert sur Python runtime.
2. Produire l'audit DB production agrégé : integrity, FK, migrations, volumes, dates, nulls/doublons.
3. Rendre visibles heartbeat worker, derniers runs, erreurs, coverage et vrais `N/A`.
4. Valider l'autorité CA/paiement et bloquer tout double comptage.
5. Exécuter un smoke authentifié read-only et un restore drill.

### 30 jours — fiabiliser le métier
1. Achever backfills PrestaShop/ShopCaisse/SumUp et mesurer couverture.
2. Résoudre Qonto puis valider chaîne paiement→payout→banque.
3. Recalculer settlements historiques avec revue des conflits.
4. Piloter CRM sur données minimisées/consenties avec métriques de qualité.
5. Ajouter E2E navigateur, accessibilité et exports sécurisés.

### 90 jours — industrialiser
1. Registre de migrations global, gates startup/deploy et rollback éprouvé.
2. Observabilité/SLO/alerting web-worker-connecteurs structurés.
3. Providers sociaux réels uniquement après conformité et sandbox.
4. Atomicité ingestion/curseurs (outbox ou transaction commune).
5. Remplacer la roadmap binaire par niveaux de preuve auditables.

## 22. Annexes techniques

### A. Commandes et preuves exécutées

```bash
git status --short --branch
git log --oneline --decorate -25
find . -name AGENTS.md -print
pytest -q
python -m compileall -q src tests
git diff --check
find src/dr_cloud_sync/static -name '*.js' -print0 | xargs -0 -n1 node --check
find . -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
rg -n -i '(api[_-]?key|secret|token|password|authorization|credential)' --glob '!docs/audits/**' .
rg -n '^(<<<<<<<|=======|>>>>>>>)' .
curl --max-time 15 https://osdrcloud.fr/{health,login,...}
curl -I --max-time 15 http://osdrcloud.fr/
```

### B. Limites explicites

* Aucun credential de production, session navigateur, accès SSH, artifact DB, secrets GitHub ou logs OVH disponibles.
* Aucun accès Data Hub authentifié : toutes ses métriques production restent `UNKNOWN`.
* Pas de test intrusif, mutation, publication, import, sync manuelle ou déploiement.
* Historique local observé jusqu'aux merges #127; détails GitHub/PR distants indisponibles car le clone n'a pas de remote configuré.
* L'audit SQLite demandé sur **production** ne peut être honnêtement remplacé par une base fixture. Les PRAGMA seront validés sur une base locale de contrôle dans les validations, marqués comme tels.

### C. État compact

* **Score : 48/100.**
* **Réellement opérationnels prouvés :** Core Application, Authentication, Deployment.
* **Partiels/fondations :** majorité des métiers, Data Hub, finance, settlements, marketing, CRM, worker.
* **Bloqués :** Qonto (jusqu'à health réel), publication sociale.
* **Absents :** providers Meta/Instagram/TikTok/Snapchat concrets; notifications externes complètes.
* **Roadmap :** 74,2 % affiché contre 46 % audité.
