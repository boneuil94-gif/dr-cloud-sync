# GRAND AUDIT V2 — DrCloud OS

**Date de coupe : 2026-08-10 · branche d'audit : `audit/grand-audit-v2` · score global strict : 58/100**

> Cet audit est une photographie du dépôt, pas une attestation du serveur OVH. L'information « Qonto CONNECTED · FRESH, 2 718 transactions » est une preuve historique fournie par le commanditaire, non revalidée depuis cet environnement. Aucun secret ni accès à la base de production n'était disponible. Ainsi, aucune volumétrie de production autre que ce point Qonto ne peut être affirmée et aucun taux de rapprochement réel ne peut être calculé.

## 1. Méthode et vocabulaire

Score fonctionnel : **20 % code/architecture, 20 % branchement, 20 % tests, 25 % preuve production, 10 % observabilité, 5 % documentation**. Un score est la somme des preuves réellement disponibles, avec crédit partiel au sein de chaque axe. Un module sans données de production est plafonné de fait sous 75 ; un provider non connecté à son API officielle n'est jamais `PRODUCTION_PROVEN`.

Statuts : `IMPLEMENTED` (code exécutable), `WIRED` (atteignable par runtime/API/job), `TESTED`, `PRODUCTION_PROVEN`, `PARTIAL`, `SIMULATED`, `DEAD_CODE`, `BLOCKED`, `NOT_CONFIGURED`, `MISSING`. `PRODUCTION_PROVEN` exige une preuve datée et observable, pas un test à double.

## 2. Résumé exécutif

| Dimension | Score | Diagnostic |
|---|---:|---|
| Code maturity | 72 | Domaine riche, SQLite/repositories/idempotence réels ; composition WSGI monolithique et schémas dispersés. |
| Production maturity | 47 | CI/CD OVH et health existent ; seule Qonto a une preuve historique, restauration/rollback/monitoring non exercés ici. |
| Business completeness | 55 | Catalogue, stock, achats, ventes et ledgers substantiels ; couverture métier réelle inconnue. |
| Security | 74 | Auth, sessions, RBAC fail-closed, CSRF et audit présents ; absence de preuve de scan dépendances/pentest/rotation. |
| Observability | 61 | health, diagnostics, batches et journaux ; pas de métriques/alerting externe/SLO démontrés. |
| Test quality | 76 | 564 tests collectés, 563 passés ; surtout locaux avec doubles, aucune validation production-like complète. |
| UX | 60 | shell/pages/états présents ; validation mobile, accessibilité et tests E2E réels absents. |

Le score global **58/100** n'est pas la moyenne naïve des fichiers : il privilégie la preuve de fonctionnement et la maturité opérationnelle. DrCloud OS est une base applicative large et testée localement, mais pas encore un SaaS professionnel prouvé de bout en bout.

## 3. Architecture — 68/100

- **IMPLEMENTED** : séparation en domaines (`sales`, `stock`, `purchasing`, `crm`, `finance`, `marketing`), repositories, ports providers, jobs, API WSGI et assets UI. Les contraintes et 72 déclarations d'index montrent une attention à l'intégrité/performance.
- **PARTIAL** : `inventory_web.py` (1 008 lignes) compose les dépendances, initialise des schémas et route une très grande API par chaîne de `if`; fort couplage HTTP/runtime/domaines et risque de régression. `domain.py`, `shopcaisse.py` et `media_import.py` sont également volumineux.
- Les migrations/schémas sont répartis entre modules au démarrage ; pas de moteur de migration transactionnel versionné unique ni d'adapter PostgreSQL. SQLite reste à la fois stockage, queue et verrou de concurrence.
- Duplication visible entre les ledgers/reconciliations (`reconciliation.py`, `financial_reconciliation.py`, `settlements.py`) et entre anciennes chaînes catalogue (`hydration.py`, `rehydration.py`, `admin_rehydration.py`, `exception_rebuild.py`). Une analyse statique dédiée des cycles n'est pas configurée.
- Aucun mock réseau manifeste n'a été trouvé hors tests, mais des adapters volontairement désactivés/simulés existent (social, banque fallback, creative déterministe).

## 4. Core — 72/100

Configuration par environnement et secret references, lifecycle applicatif, contraintes SQLite, migrations testées, logs/audits, health, scheduler, retry et clés d'idempotence sont **IMPLEMENTED/TESTED**. Le batch `RUNNING` a un index unique et les jobs persistent leurs runs. Limites : migrations distribuées, absence de preuve de reprise après crash, politique de retry non uniformisée, SQLite locking/concurrence multi-worker non éprouvés, pas de traces distribuées ni métriques de durée/mémoire/SLO. La fonction `FINANCE` et plusieurs refresh retournent explicitement `rows_imported: 0`, ce qui limite l'observabilité métier.

## 5. Production et déploiement — 66/100

- **IMPLEMENTED/TESTED statiquement** : CI PR/main (pytest, compileall, `node --check`, shell syntax, Compose, image), déploiement du SHA CI exact vers OVH, Caddy/HTTPS, health/check, migrations au démarrage, secrets hors Git, scripts backup/restore et état de déploiement.
- **NON PROUVÉ ICI** : dernier commit effectivement servi, certificat/health public, exécution migration production, restauration d'une sauvegarde, RPO/RTO, rollback exercé, monitoring externe/alertes et rotation des secrets.
- Le test Compose est le seul skip local faute de Docker Compose. La présence de runbooks n'est donc pas une preuve de recovery.

## 6. Authentication / Security — 74/100

**Forces** : mots de passe hachés, sessions persistantes/révocables, RBAC et autorisation centrale fail-closed, séparation read/write/admin, CSRF sur mutations, headers de sécurité, validation HTTPS provider, références de secrets, audit log et masquage CRM. Les exports passent par permission.

| Priorité | Risque | Constat / action |
|---|---|---|
| P0 | Accès/données production non vérifiables | Aucun P0 exploitable confirmé dans le code ; vérifier immédiatement en production secrets, compte bootstrap, HTTPS et sauvegarde restaurable. |
| P1 | Perte de données | Recovery/rollback non exercés et SQLite partagé : test de restauration + verrouillage + copie cohérente obligatoires. |
| P1 | Exposition PII | PII CRM et exports existent ; prouver rétention, consentement, droit d'effacement, chiffrement disque et accès opérateur. |
| P2 | Chaîne logicielle | Seulement deux dépendances runtime épinglées et Dependabot ; ajouter scan CVE/SBOM, secret scan et politique de mise à jour. |
| P2 | Abus | Rate limiting/login throttling à valider en charge et derrière proxy ; absence de WAF/alerting prouvé. |
| P3 | Assurance | Ajouter pentest, tests headers en environnement public et revue périodique RBAC/audit. |

## 7. Data Hub — 65/100

Sources, evidence, Sync All/retry, batch history, freshness, diagnostics de schéma/connecteurs et état worker sont **WIRED/TESTED**. Le statut initial distingue `NOT_CONFIGURED`/`UNAVAILABLE`; les jobs et dépendances sont visibles. Mais `FRESH` dépend des derniers runs stockés localement : sans télémétrie externe il ne prouve ni exhaustivité ni vérité de la source. Les jobs sociaux sont enregistrés alors que les providers sont désactivés. La couverture est un indicateur applicatif, non rapproché à des totaux d'autorité. Aucune preuve runtime du Sync All complet n'était accessible.

## 8. Connecteurs métier

### ShopCaisse — CODE 72 %, PRODUCTION 38 %, score strict 58/100

Client API, authentification, pagination ventes, CSV/inbox, parsing, ventes/paiements, paiements multiples/mixte, historique, idempotence, erreurs persistées et settlement sont **IMPLEMENTED/WIRED/TESTED**. Les artifacts d'import catalogue sont des **SIMULATED dry-runs**; le writer réseau n'est volontairement pas possédé. Aucun total d'autorité ShopCaisse, taux de pagination exhaustive ou couverture production n'est disponible. Les paiements inhabituels restent dépendants de formes payload observées.

### PrestaShop — 64/100

| Sous-module | État | Score | Verdict |
|---|---|---:|---|
| Catalog | WIRED/TESTED, PROD non prouvé à la coupe | 72 | pagination, variantes, mapping, réhydratation/idempotence ; catalogue/artifacts historiques présents, exhaustivité live inconnue. |
| Sales | WIRED/TESTED, PARTIAL | 66 | commandes/clients et états payés configurables ; configuration des paid states et couverture production non prouvées. |
| Media | IMPLEMENTED/TESTED, PARTIAL | 54 | import, checksum, variantes et primary media ; synchronisation officielle production et couverture images non prouvées. |

### SumUp — 58/100

Transactions et payouts ont providers, pagination/curseur, stockage, idempotence, sync et rapprochement **WIRED/TESTED**. Merchant, fees, refunds, chargebacks et readers ont sources/endpoints/schema préparés, mais les handlers Sync All ne branchent réellement que `SUMUP_TRANSACTIONS` et `SUMUP_PAYOUTS` : les autres sont **PARTIAL / CODED_NOT_WIRED**. Aucun merchant code, volume, freshness ou résultat production n'est disponible. Matching/settlement est calculé localement, non production-proven.

### Qonto — 78/100

Health organisation, accounts, transactions paginées, curseur/idempotence, Bank Ledger, balance, classification, matching, Finance et job Sync All sont **IMPLEMENTED/WIRED/TESTED**. Preuve historique déclarée : **CONNECTED · FRESH, 2 718 transactions**; elle crédite la donnée production mais pas chaque sous-module. Aucun snapshot daté/attaché ne permet de revalider solde, exhaustivité pagination, classification, matching ou fraîcheur actuelle. Le diagnostic Cloudflare 1010 existe.

## 9. Finance — 61/100

Cockpit, revenue/canaux/paiements, marge/coûts FIFO, banque, cashflow, cost coverage, top products, freshness et états partiels sont codés. Sources d'autorité : revenue/produits = Sales Ledger; paiements = `sale_payments`; banque/solde = Qonto Bank Ledger; coûts/marge = purchase cost events/FIFO allocations; stock value = cost lots; settlement = SumUp/Qonto ledgers. Les KPI ne sont fiables que dans la limite de leur coverage exposée. Aucun rapprochement aux comptes, période clôturée, TVA/comptabilité ou validation production du cockpit n'est fourni.

## 10. Settlement / Reconciliation — 57/100

La chaîne `SALE → PAYMENT → SUMUP TRANSACTION → PAYOUT → QONTO` existe en modèles, indexes, algorithmes, cockpit/explorer, confirmation/rejet et audit. Les statuts du code sont `MATCHED`, `POSSIBLE`, `UNMATCHED`, `CONFLICT`, `REJECTED` plutôt que le vocabulaire demandé `PROBABLE/AMBIGUOUS`; ce défaut sémantique doit être documenté/corrigé. **Taux réel non calculable** : aucune base production n'est fournie. Donc MATCHED/PROBABLE/AMBIGUOUS/UNMATCHED/anomalies/coverage = **N/D à cette coupe**, et non zéro.

## 11. Stock — 67/100

Stock actuel/projection, mouvements, ventes, réceptions/incoming, négatifs, mapping produits, valeur FIFO, coverage, comparaison externe et anomalies sont **IMPLEMENTED/WIRED/TESTED**. Manquent la preuve de stocktake terrain, l'exhaustivité mapping et la convergence production PrestaShop/ShopCaisse. Les écritures externes restent safe-mode/contrôlées.

## 12. Purchases — 63/100

Fournisseurs, commandes, lignes, réceptions, coûts, FIFO, suggestions/réapprovisionnement, délais et budget existent localement. Les suggestions sont calculées, pas des commandes fournisseur automatiques; invoices CSV sont preview/apply avec validation humaine. **Réel local** : achats saisis et reçus. **SIMULATED/NOT_CONFIGURED** : connecteurs fournisseurs, prix/délais officiels et commande externe. Aucune couverture production n'est mesurable.

## 13. CRM — 52/100

Customer 360, identité/déduplication, rattachement aux ventes, RFM, segments, consent evidence, actions de réactivation, fidélité et attribution sont codés. Fidélité et activation restent explicitement **SIMULATED**; aucune sollicitation externe. Customer coverage, revenue coverage et consent coverage sont calculables par le cockpit en runtime, mais **N/D sans base production**. `UNKNOWN` ne vaut jamais consentement.

## 14. Marketing, Social et Analytics

- **Marketing 47/100** : foundation, signals ventes/stock/achats, creative déterministe, human review, calendrier/pipeline interne, analytics/learning/cockpit = `INTERNAL_READY`. Ports/prérequis = `PROVIDER_READY` au sens interface seulement. Publication officielle et boucle production = jamais `PRODUCTION_READY`.
- **Social 22/100** : Instagram, Facebook, TikTok, Snapchat sont chacun `NOT_CONFIGURED/BLOCKED`; `DisabledSocialProvider` et sources analytics `NOT_CONFIGURED`. Aucun API provider officiel en production, donc 0 provider production-ready.
- **Analytics 51/100** : ventes/produits/clients/stock/marge/finance/marketing lisent les ledgers locaux; prévisions sont heuristiques/partielles. Aucun dataset fictif dur n'a été identifié dans les dashboards, mais données vides, projections et simulations doivent rester labellisées et ne prouvent pas la prod.

## 15. IA — 35/100

| Usage | Provider / fallback | Données, coût, revue | Production |
|---|---|---|---|
| Creative copy/visual plan | `DeterministicCreativeGenerator`, aucun LLM/API | faits produit locaux; coût provider nul; human review avant planning | `SIMULATED/INTERNAL_READY` |
| Marketing intelligence | règles/scoring local | ventes, stock, marge agrégés; coût nul; validation humaine | `WIRED`, pas IA générative |
| Réhydratation/mapping | règles/fuzzy déterministes | catalogue local; coût nul; ambiguïtés revues | `IMPLEMENTED`, ne pas appeler « IA » |

Aucun OpenAI/Anthropic/provider génératif configuré. L'« AI » créative est **IA décorative au sens commercial** : utile comme générateur déterministe testable, mais pas un service IA externe ni un usage production prouvé. Aucun PII n'est envoyé à un provider puisque aucun provider n'est branché.

## 16. UX — 60/100

Navigation centralisée, pages Data Hub/Finance/Stock/CRM/Marketing, empty/error/loading states, tableaux, filtres et certains drill-down sont présents. Les assets sont syntax-checkés et les routes testées. Faiblesses : JS/HTML largement sans framework et parfois minifié en une ligne, pas de suite navigateur E2E/visuelle/accessibilité, validation mobile réelle absente, tableaux complexes potentiellement difficiles au clavier/petit écran. Pages techniquement présentes mais métier inutilisable en prod : social analytics/publishing sans provider, fidélité/réactivation sans sortie, creative AI déterministe, dashboards vides lorsque les sources ne sont pas configurées.

## 17. Performance — 59/100

72 créations d'index, pagination providers et verrous/idempotence limitent certains risques. Points à mesurer : multiples agrégations SQLite dans cockpits, `_rows`/listes complètes et calculs Python, gros import/recompute settlement, rendu de grands tableaux, migrations au démarrage. Risques : scans complets, requêtes privées appelées directement depuis HTTP, contention writer SQLite, scheduler et requêtes web sur la même base, absence de budget mémoire/durée, EXPLAIN/slow-query log/load test. Aucun N+1 réseau évident n'est prouvé, mais les hydrations/media nécessitent profiling.

## 18. Tests — 76/100

Commande complète exécutée : **564 collectés ; 563 passed ; 0 failed ; 1 skipped** en 389,63 s (Python 3.14.4). Le skip est `tests/test_os_production.py:365`, Docker Compose indisponible. Inventaire : unitaires de domaines, intégration SQLite/repositories, API WSGI, migrations, régressions, sécurité et assets. Les tests provider utilisent des doubles; les tests « production » valident surtout configuration/scripts. Zones faibles : E2E navigateur/mobile/accessibilité, charge/concurrence/locking, fault injection réseau, restauration réelle, rollback, API officielles sandbox/live, production-like multi-worker et couverture quantitative publiée.

## 19. Documentation — 70/100

Architecture, setup/déploiement, providers, migrations/domaines, KPI finance, runbooks backup/restore et sécurité sont largement documentés. Manquent preuves d'exécution datées pour recovery/incidents, catalogue de SLO/alertes, ownership/on-call, matrice KPI machine-vérifiable et inventaire officiel des migrations. Plusieurs documents décrivent une intention ou une « livraison » sans preuve production.

## 20. Dead code et chemins anciens (candidats, ne rien supprimer sans traçage)

- `qonto_diagnostic.py` : aucun import interne détecté; peut être CLI/outil manuel, à confirmer avant classement définitif.
- `bank.UnavailableBankProvider` : placeholder honnête/fallback, actif seulement lorsque Qonto manque; **not configured path**, pas production feature.
- `connectors.DisabledConnector` et `social.DisabledSocialProvider` : adapters sentinelles utiles, mais ne sont pas des providers livrés.
- Chaînes qui se chevauchent `hydration.py`, `rehydration.py`, `admin_rehydration.py`, `exception_rebuild.py` : anciens chemins/candidats consolidation; usages/tests existent pour plusieurs, donc pas suppression automatique.
- `reconciliation.py`, `financial_reconciliation.py`, `settlements.py` : trois générations/couches de rapprochement, toutes potentiellement branchées; dette/duplication plutôt que dead code prouvé.
- Artifacts `dist/*ean*`, plans et dry-runs ShopCaisse : données/outils historiques, **SIMULATED**, pas runtime production.
- Aucun fichier définitivement `DEAD_CODE` n'est démontré par la seule recherche textuelle; routes « jamais appelées » nécessitent télémétrie production absente.

## 21. Faux pourcentages de la roadmap V2

**OVERRATED** : Catalogue 90 (preuve live/exhaustivité et PostgreSQL absents), Stock 90 (validation terrain absente), Achats annoncé jusqu'à 100 dans l'historique (connecteurs/couverture absents), Production 76,47 (recovery/rollback/monitoring non prouvés), Marketing jusqu'à 78,57 (providers officiels bloqués), Ventes 76,92 (couverture ShopCaisse/Presta production inconnue).

**UNDERRATED** : Sécurité historique 38,46 est dépassée par RBAC fail-closed, sessions, CSRF et audit désormais présents; Qonto mérite le crédit de la preuve historique 2 718 transactions.

**ACCURATE dans l'esprit** : Dashboard historique faible/partiel; les documents qui maintiennent explicitement Social bloqué et CRM loyalty simulé; global V2 53,62 était une progression de jalons, pas une maturité production et ne doit plus être comparé directement au 58 strict.

## 22. Ce qui bloque 90 / 95 / 100

- **90** : prouver tous les connecteurs essentiels avec totaux d'autorité; taux settlement/CRM/stock; backups restaurés; rollback exercé; monitoring/SLO; tests E2E, charge et multi-worker; supprimer les faux `FRESH` sans exhaustivité.
- **95** : clôture/comptabilité contrôlée, disaster recovery chronométré, sécurité indépendante/pentest, canary/rollback automatisé, providers sociaux officiels nécessaires au périmètre, data quality contractualisée.
- **100** : aucune dette/risque connu n'est réaliste durablement. Il faudrait en plus preuves continues, zéro blocage critique, documentation et runbooks exercés, compatibilité production multi-instance et validation métier complète. 100 doit rester un état temporaire auditable, jamais une promesse.

## Addendum de preuves — Production Truth & Recovery Pack

Le 10 août 2026 à 13:48:07Z, `/health` public a répondu 200/ok et exposé `6798ab3f156e7c644d45d331f2586998baecf317`, identique au SHA main/déployé attendu. HTTPS et la redirection 308 sont prouvés; CSP, frame, nosniff et Referrer-Policy sont présents, HSTS absent. Cette preuve remplace uniquement les mentions « commit/health public non testé » : Production maturity **47 → 49**, Deployment **66 → 68**, global strict **58 → 58**. Backup/restore/rollback/RPO/RTO, données privées et funnel restent `NOT_PROVEN`. Voir `PRODUCTION_TRUTH.md` et la capture JSON datée.
