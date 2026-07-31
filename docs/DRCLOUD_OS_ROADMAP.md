# Roadmap officielle DrCloud OS

**Version :** 2.0.0 — **audit du dépôt :** 2026-07-31 — **progression calculée : 49,30 %** — **reste : 50,70 %**.

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
| Ventes | 5 % | IN_PROGRESS | 8 | frontière documentée à moitié du premier jalon ; aucun Sales Ledger ni imports de ventes locaux. |
| Finance + pilotage | 0 % | TODO | 8 | Sales Analytics, marges, TVA, valeur de stock et rentabilité restent à construire. |
| Clients + fidélité | 0 % | TODO | 6 | modèles, consentements, rapprochement et fidélité absents. |
| Marketing + réseaux sociaux | 33,33 % | IN_PROGRESS | 8 | Marketing Foundation, Creative AI v1, Human Review et Social Publishing Pipeline v1 sont livrés ; providers/conformité/publication réels, analytics live et intelligence pilotée par ventes/stock/achats restent séparés. |
| Automatisations + IA | 5 % | IN_PROGRESS | 8 | règles READ/PROPOSE/EXECUTE documentées ; permissions, moteur générique, déclencheurs, actions et assistant restent futurs. |
| Dashboard | 20 % | IN_PROGRESS | 5 | frontière et roadmap dynamique présentes ; projections métier live absentes. |
| Sécurité + utilisateurs | 38,46 % | IN_PROGRESS | authentification et credentials durables présents ; RBAC et autorisation par action absents. |
| Production + PWA | 76,47 % | IN_PROGRESS | Docker, PWA, sauvegardes, observabilité, CI/CD et administration présents ; domaine, rollback UI/automatique et monitoring externe incomplets. |
| **Total** | **49,30 %** | **IN_PROGRESS** | **100** | Somme pondérée calculée depuis les jalons. |

## Ancien pourcentage global

**47,43 %**. Le service recalculait déjà ce nombre, mais depuis une liste ancienne. Le JSON et la documentation contenaient aussi des copies dérivées obsolètes, ce qui rendait l'audit trompeur même si l'API écrasait la copie au chargement.

## Nouveau pourcentage global calculé

**49,30 %**.

## Pourquoi il change

Les merges Creative AI/Human Review et Social Connections/Publishing n'étaient jamais entrés dans les jalons Marketing : ce domaine restait décrit comme un simple `ProductMedia → MarketingAsset`. L'audit reconnaît maintenant quatre livraisons v1 prouvées par le code, les routes, le cockpit et les tests. Il conserve explicitement à zéro les providers sociaux réels, la conformité officielle, l'activation de publication, les analytics live, Sales-driven/Stock-driven Marketing, l'intelligence achats/marge et le Learning Loop. La hausse est donc seulement la conséquence déterministe de la roadmap corrigée, pas une valeur saisie à la main.

## Marketing : frontière v1 / production

Creative AI v1 est provider-neutral : son générateur déterministe produit des spécifications/copies PREVIEW à partir du produit canonique et du média PRIMARY, en STORY/SQUARE, avec `PRESERVE_ORIGINAL`, revue approve/reject, cockpit et audit. Cela ne prétend pas fournir un fournisseur génératif externe. Social v1 fournit connexions/capabilities, scheduler, orchestration, idempotence/concurrence, compliance gate, historique et UI en mode sûr. Les adapters et secrets réels sont désactivés et la conformité non configurée échoue fermée : la publication de production demeure donc `BLOCKED`.

## Maintenance

Lorsqu'un bloc est livré, modifier dans la même PR le jalon canonique et son `evidence`. Passer à `DONE` seulement après vérification du code, de la persistance, des routes/UI pertinentes et des tests. Pour un travail partiel, ajouter des `steps` binaires et utiliser `IN_PROGRESS`. Ajouter un futur résultat comme jalon distinct plutôt que d'élargir après coup une fondation terminée. Ne jamais écrire un pourcentage dans JSON, Python, HTML ou JavaScript : il est une projection de `RoadmapService`.

## Prochain ordre de livraison

Construire d'abord le Sales Ledger et ses imports idempotents, puis Sales Analytics. Ces faits pourront alimenter Finance, Sales-driven Marketing et le Learning Loop. En parallèle, le passage social en production exige des providers homologués, une conformité métier officielle et une activation contrôlée ; Social Analytics live vient seulement après des publications réelles. L'intelligence stock et achats/marge doit rester fondée sur les ledgers existants, sans confondre ports/interfaces et décisions métier.
