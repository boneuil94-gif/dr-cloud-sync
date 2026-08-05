# Roadmap officielle DrCloud OS

**Version :** 2.0.0 — **audit du dépôt :** 2026-07-31 — **progression calculée : 53,62 %** — **reste : 46,38 %**.

## Source d'autorité et calcul

La seule source d'autorité exécutable est [`drcloud-os-roadmap.json`](drcloud-os-roadmap.json). Elle contient uniquement les faits versionnés (domaines, poids, jalons, états, sous-étapes et blocages) : aucun pourcentage calculé n'y est stocké. `RoadmapService` valide puis calcule l'API, l'écran Roadmap et le Dashboard depuis ce fichier.

Les poids des 13 grands domaines totalisent exactement 100. Ils représentent leur étendue métier relative ; ils n'ont pas été retouchés pour atteindre un résultat souhaité. Dans un domaine, les jalons sont des unités de livraison de même rang : `DONE` vaut 1, `TODO` et `BLOCKED` valent 0. `IN_PROGRESS` vaut strictement la proportion de ses sous-étapes binaires terminées, jamais un 50 % décoratif. La progression d'un domaine est la moyenne de ses jalons et la progression globale est `Σ(poids du domaine × progression du domaine)`.

Définitions :

- **DONE** : définition du jalon utilisable, persistée si nécessaire, exposée par ses routes/UI si prévu et couverte par des tests ;
- **IN_PROGRESS** : résultat partiellement utilisable, détaillé par des sous-étapes constatables ;
- **TODO** : aucun crédit ;
- **BLOCKED** : aucun crédit et raison visible. Une fondation ne rend jamais son intégration de production `DONE`.

## Résultat de l'audit

| Domaine | Progression | État | Poids | Justification |
| ------- | ----------: | ---- | ----: | ------------- |
| Core + architecture | 70 % | IN_PROGRESS | 10 | Domaines, repositories, services, événements, idempotence et jobs présents ; notifications, permissions/audit complet et configuration centralisée restent à livrer. |
| Catalogue + mapping | 90 % | IN_PROGRESS | 10 | Catalogue durable, identité commerciale, variantes/EAN, recherche, mapping et tests présents ; adapter PostgreSQL absent. |
| Inventaire + EAN | 80 % | IN_PROGRESS | 10 | Sessions, scan, comptage, exports et workflow de rapprochement présents ; validation terrain et activation live absentes. |
| Stock + synchronisation | 90 % | IN_PROGRESS | 10 | mouvements, ledger, projections, idempotence et observation PrestaShop contrôlée présents ; alertes de stock faible absentes. |
| Achats + fournisseurs | 80 % | IN_PROGRESS | 10 | fournisseurs, commandes, réceptions, validation et propositions de mouvements présents ; rapprochement catalogue et analyse prix/TVA absents. |
| Ventes | 76,92 % | IN_PROGRESS | 8 | Modèle opérationnel canonique, ingestion ShopCaisse CSV preview-first, commandes PrestaShop GET-only à états encaissés configurables, mapping persistant et alimentation automatique du Sales Ledger livrés ; réseau ShopCaisse et retours source complets restent partiels. |
| Finance + pilotage | 0 % | TODO | 8 | Les analytics du Sales Ledger existent, mais marges fiables, TVA, valeur de stock et rentabilité Finance restent à construire. |
| Clients + fidélité | 0 % | TODO | 6 | modèles, consentements, rapprochement et fidélité absents. |
| Marketing + réseaux sociaux | 46,15 % | IN_PROGRESS | 8 | Les quatre fondations précédentes, Sales-driven Marketing v1 et la fondation Social Analytics provider-neutral sont livrés ; providers/conformité/publication réels, analytics sociaux live et intelligence stock/achats restent séparés. |
| Automatisations + IA | 5 % | IN_PROGRESS | 8 | règles READ/PROPOSE/EXECUTE documentées ; permissions, moteur générique, déclencheurs, actions et assistant restent futurs. |
| Dashboard | 20 % | IN_PROGRESS | 5 | frontière et roadmap dynamique présentes ; projections métier live absentes. |
| Sécurité + utilisateurs | 38,46 % | IN_PROGRESS | authentification et credentials durables présents ; RBAC et autorisation par action absents. |
| Production + PWA | 76,47 % | IN_PROGRESS | Docker, PWA, sauvegardes, observabilité, CI/CD et administration présents ; domaine, rollback UI/automatique et monitoring externe incomplets. |
| **Total** | **53,62 %** | **IN_PROGRESS** | **100** | Somme pondérée calculée depuis les jalons. |

## Ancien pourcentage global

**49,30 %**, dont **Marketing 33,33 %** et **Ventes 5 %**.

## Nouveau pourcentage global calculé

**53,62 %**, dont **Marketing 46,15 %** et **Ventes 46,15 %**.

## Pourquoi il change

Les poids sont inchangés. Marketing compte désormais 6 jalons `DONE` sur 13, soit `6 / 13 × 100 = 46,15 %` ; son apport pondéré passe de `8 × 33,33 % = 2,67` à `8 × 46,15 % = 3,69`. Ventes compte également 6 capacités analytiques `DONE` sur 13, soit `6 / 13 × 100 = 46,15 %` ; son apport passe de `8 × 5 % = 0,40` à `8 × 46,15 % = 3,69`. Avec les arrondis appliqués par `RoadmapService`, le total devient `49,30 - 0,40 - 2,67 + 3,69 + 3,69 = 53,62 %`.

L'audit de #78 confirme un `SaleEvent` source-neutral, un ledger SQLite append-only, des contraintes d'idempotence et un audit, un import CSV manuel PREVIEW/APPLY, le rapprochement canonique exact, les métriques 7/30 jours et leur cockpit, ainsi que des snapshots Social Analytics provider-neutral persistants aux métriques nullables. Il confirme également l'utilisation réelle du Sales Ledger par Marketing pour les signaux `BEST_SELLER`, `SALES_SPIKE`, `SALES_DROP` et `TRENDING_PRODUCT`, le score de fatigue, les opportunités et propositions testées. Cela justifie `Sales-driven Marketing v1 = DONE`.

Ventes v1 ajoute désormais le modèle opérationnel et le flux automatique vers ce même ledger. PrestaShop est lu en GET-only avec une politique d’états encaissés explicite. Aucun endpoint réseau de ventes ShopCaisse n’étant vérifié dans le dépôt, seule l’ingestion d’un export CSV réel avec PREVIEW/APPLY est créditée; l’intégration réseau reste partielle. Les paiements et l’observation exhaustive des remboursements/retours restent futurs.

## Marketing : frontière v1 / production

Creative AI v1 est provider-neutral : son générateur déterministe produit des spécifications/copies PREVIEW à partir du produit canonique et du média PRIMARY, en STORY/SQUARE, avec `PRESERVE_ORIGINAL`, revue approve/reject, cockpit et audit. Cela ne prétend pas fournir un fournisseur génératif externe. Social v1 fournit connexions/capabilities, scheduler, orchestration, idempotence/concurrence, compliance gate, historique et UI en mode sûr. Les adapters et secrets réels sont désactivés et la conformité non configurée échoue fermée : la publication de production demeure donc `BLOCKED`.

## Maintenance

Toute PR qui termine un milestone roadmap doit mettre à jour la roadmap canonique et ses tests dans la même PR. Lorsqu’un bloc est livré, modifier aussi son `evidence`. Passer à `DONE` seulement après vérification du code, de la persistance, des routes/UI pertinentes et des tests. Pour un travail partiel, ajouter des `steps` binaires et utiliser `IN_PROGRESS`. Ajouter un futur résultat comme jalon distinct plutôt que d'élargir après coup une fondation terminée. Ne jamais écrire un pourcentage dans JSON, Python, HTML ou JavaScript : il est une projection de `RoadmapService`.

## Prochain ordre de livraison

Valider ensuite le format ShopCaisse sur export terrain et compléter les faits de remboursement/retour réellement exposés par les sources. Le Sales Ledger analytique et Sales-driven Marketing v1 sont livrés, mais ne couvrent ni Finance ni le Learning Loop mesuré. En parallèle, le passage social en production exige des providers homologués, une conformité métier officielle et une activation contrôlée ; Social Analytics live vient seulement après des publications réelles. L'intelligence stock et achats/marge doit rester fondée sur les ledgers existants, sans confondre ports/interfaces et décisions métier.

## Progression de Ventes v1

Les valeurs sont calculées depuis les jalons pondérés par `RoadmapService`, jamais stockées dans le JSON canonique : **progression globale 53,62 % → 56,08 %** et **Ventes 46,15 % → 76,92 %**.

## Livraison Data Hub V1 — évolution calculée

Les pourcentages ne sont pas écrits dans cette documentation : `/api/roadmap` les recalcule depuis les poids et statuts du manifeste. La comparaison avant/après est donc présentée en capacités vérifiables :

| Domaine | Avant | Après cette livraison |
|---|---|---|
| Global | Ledgers et jobs isolés | Plan de contrôle Data Hub et chaînes de dépendances |
| Finance | Module futur | Bank Ledger, rapprochement conservateur et projection cashflow en lecture seule |
| Dashboard | Ventes et système | Contrats de projection banque/achats/stock/alertes préparés |
| Automatisations | Jobs reprenables isolés | Scheduling configurable, claim, retry/backoff, curseur et freshness |
| Ventes | Sales Ledger et imports | Source PrestaShop réelle déclarée seulement si configurée ; ShopCaisse reste honnêtement non configuré hors import |

Qonto n'est **pas connecté** : son port testable est livré, mais l'activation attend les credentials secrets et un contrat officiel validé. L'alerting externe avec cooldown demeure une étape ultérieure ; le cockpit expose dès maintenant les signaux sources/jobs.

## Security Hardening V1 — audit honnête

Le modèle User/Role/Permission, la matrice RBAC, l'autorisation centralisée fail-closed, les sessions persistées/révocables, la gestion des utilisateurs, le cockpit et les tests ciblés sont livrés. Le domaine n'est volontairement **pas déclaré à 100 %** : l'AuditLog central ne remplace pas encore chaque journal métier historique, l'administration de `SystemSetting` n'a pas encore son API/UI, et les connecteurs historiques PrestaShop/ShopCaisse ne consomment pas tous une `secret_ref`. Ces trois jalons restent `IN_PROGRESS` avec sous-étapes vérifiables dans la roadmap canonique.

## Purchase Cost Ledger & Profitability V1 — 2026-08-01

Progression calculée depuis les jalons (aucun pourcentage dans le JSON): **globale 63,48 % → 68,79 %**, **Achats 80 % → 100 %**, **Finance 50 % → 80 %**, **Dashboard 27,27 % → 45,45 %**, **Marketing 46,15 % → 46,15 %**. Achats gagne le rapprochement exact et l'analyse prix/TVA. Finance gagne coûts d'achat, TVA disponible, valeur du stock et rentabilité produit. Dashboard consomme les projections marge/stock. La rentabilité catégorie, les achats/remboursements Finance, la projection réceptions Dashboard et Purchase/margin intelligence Marketing restent volontairement non livrés.

## External Platforms Activation V1 — avant / après audité

Cette livraison ne crédite aucun port comme une connexion live. Les statuts binaires du manifeste restent donc inchangés et les pourcentages calculés par `RoadmapService` ne progressent pas artificiellement.

| Domaine | Avant | Après V1 |
|---|---|---|
| Global | connecteurs réels hétérogènes | inventaire commun, états/freshness/cadences observables; progression calculée inchangée |
| Ventes | ShopCaisse inbox PARTIAL; PrestaShop payé | politique annulation/remboursement explicite, limites paiements documentées; jalons inchangés |
| Finance | Bank/Purchase Cost ledgers et rapprochement | chaîne Qonto confirmée read-only; rapprochement complet reste futur |
| Marketing | fondation analytics, aucun provider live | quatre sources visibles NOT_CONFIGURED; Social Analytics live reste TODO |
| Automatisations | worker Data Hub | inventaire externe et scheduling central complétés; fournisseurs/social non activés |
| Production | worker OVH et healthcheck | variables/cadences documentées; aucun déploiement effectué par cette PR |

Le prochain progrès mesurable exige un endpoint tickets ShopCaisse officiellement vérifié, des lignes de paiement/remboursement PrestaShop réelles, ou un provider fournisseur/social homologué avec health check runtime.

## Progression Marketing Intelligence (2026-08-04)

La méthode pondérée existante fait progresser le module Marketing de **46,15 % à
76,92 %** et la progression globale de **68,79 % à 72,20 %**. Social Analytics
Live, Stock-driven Marketing, Purchase/Margin Intelligence et Learning Loop mesuré
sont DONE. Providers sociaux réels homologués, conformité officielle et publication
sociale réelle restent BLOCKED.


## Marketing Operations Cockpit (2026-08-04)

La livraison du poste de pilotage fait progresser Marketing de **76,92 % à 78,57 %** et la progression globale calculée de **72,20 % à 72,33 %**. Les trois jalons providers sociaux réels, conformité officielle et publication sociale réelle restent strictement `BLOCKED`.

## Preuve production Data Hub

- Fait : le Data Hub possède une API read-only de preuve production et une vue Administration qui affiche explicitement les états non configuré, aucune donnée, indisponible et inconnu.
- Fait : la fraîcheur `CONNECTED_NO_DATA` empêche de présenter un health check réussi comme un import métier frais.
- Fait : les colonnes de preuve sont migrées de manière additive et les valeurs inconnues restent `NULL`.
