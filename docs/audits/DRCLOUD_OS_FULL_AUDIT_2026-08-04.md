# Audit complet et recalcul de maturité — DrCloud OS

**Date de référence :** 4 août 2026 (contrôles production à 15:04 UTC)
**Révision auditée :** `01922585a78832296bc30d64c09557148d2a561d`
**Périmètre :** dépôt complet, tests locaux, configuration de déploiement et contrôles HTTP publics non intrusifs.
**Conclusion courte :** **maturité globale réelle 54/100**, contre **75,93 % affichés par la roadmap**.

> Cet audit remplace les pourcentages précédents : aucune ancienne note n'a été reprise comme preuve. Les anciennes valeurs ne figurent que dans le tableau d'écarts demandé. Une route protégée qui redirige vers la connexion prouve le contrôle d'accès, pas la fonctionnalité derrière la session. Une intégration codée ou configurée n'est pas dite opérationnelle sans health et import durable observables.

## 1. Méthode et barème strict

### 1.1 Sources de preuve

1. **Code et écrans :** lecture des 100 fichiers de `src/`, dont environ 12 073 lignes Python, le serveur WSGI central, les services métier et les assets statiques.
2. **Persistance :** schéma et migrations SQLite, repositories, idempotence, journaux et sauvegardes. Aucune base de production ni export anonymisé n'était disponible.
3. **Tests :** exécution intégrale de `python -m pytest -q` : **514 réussis, 1 ignoré**, en 151,74 s. Vérifications syntaxiques Python, JavaScript, shell, Compose et Docker décrites dans la CI.
4. **Production :** requêtes HTTP vers `https://osdrcloud.fr`; comparaison du SHA public avec `HEAD`; routes privées testées sans contourner l'authentification.
5. **Livraison :** workflows GitHub, Dockerfile, Compose, scripts OVH, backup/restore et historique Git local.
6. **Documentation :** roadmap canonique et documentation métier confrontées au comportement du code; une affirmation documentaire seule ne vaut pas validation.

### 1.2 Barème imposé

| Note | Interprétation appliquée |
|---:|---|
| 0 % | Absent. |
| 25 % | Fondations ou architecture prête, sans parcours fonctionnel complet. |
| 50 % | Fonctionnel localement, avec code/UI/persistance suffisants; production métier non vérifiée. |
| 75 % | Déployé, utilisable et observable. |
| 100 % | Production validée, connecteurs réels, tests, monitoring et usage métier complet. |

Les notes de module utilisent **uniquement** ces cinq niveaux. Le score global est la moyenne pondérée de ces notes; il peut donc être intermédiaire. Un module non terminé n'est pas abaissé sous 25 % lorsque ses fondations sont solides.

### 1.3 Limites qui interdisent un faux 75/100

* Aucune session de production, base SQLite, console GitHub Actions, secrets, logs VPS, métriques, sauvegarde restaurée ni preuve d'utilisateur métier n'a été fournie.
* Les pages privées répondent `303 /login`. Leur déploiement est probable puisque le SHA est exact, mais leur rendu et leurs données ne sont pas directement vérifiables.
* Les dernières PR sont visibles dans Git, mais leurs statuts Actions et détails de déploiement ne sont pas accessibles depuis ce clone sans remote configuré.
* `https://os.drcloud.fr` répond `503 DNS resolution failure`; le domaine effectivement servi est `https://osdrcloud.fr`.

## 2. Architecture et inventaire objectif

DrCloud OS est un **monolithe modulaire Python/WSGI**, sans framework web, avec UI HTML/CSS/JavaScript servie par le même processus. `inventory_web.py` concentre routage, composition et autorisation (environ 949 lignes). Le métier est réparti entre domaine, services, repositories et adaptateurs. SQLite est l'autorité locale unique et le web et l'`automation-worker` partagent le volume `/data`.

### Points solides

* Modèles métier explicites, clés stables, ledgers append-only, idempotence, migrations additives et transactions SQLite.
* Nombreux parcours preview/validation/apply et contrôle humain avant opérations sensibles.
* API privée assez large : catalogue, inventaire, stock, achats, ventes, finance, settlements, CRM, marketing, administration et sécurité.
* Dégradation explicite des fournisseurs externes : `NOT_CONFIGURED`, `UNAVAILABLE`, `ERROR`, fraîcheur distincte de la connectivité.
* Surface de dépendances réduite (`waitress`, `Pillow`) et tests rapides sans infrastructure externe.

### Faiblesses transverses

* Le contrôleur WSGI est devenu un point de couplage et de risque; absence d'OpenAPI, de versionnement API et de validation de schéma généralisée.
* SQLite partagé convient à l'étape actuelle mais limite concurrence, haute disponibilité, analytics et montée en charge; PostgreSQL reste prévu.
* Une grande partie des tests est unitaire/intégration locale avec doubles. Il n'existe pas de suite E2E navigateur, test de charge, test de contrat planifié avec sandbox fournisseur ni mesure de couverture versionnée.
* L'observabilité est opérationnelle dans l'application (health, diagnostics, audit, worker heartbeat), mais pas une chaîne complète de métriques/alerting/traces.
* Plusieurs interfaces sont des cockpits réels sur données locales, mais les volumes et la fraîcheur métier de production restent inconnus.

## 3. Vérification de production

| Contrôle | Résultat observé | Ce que cela prouve | Ce que cela ne prouve pas |
|---|---|---|---|
| `GET /health` | `200`, `status=ok`, `database=ok`, version `1.0.0`, commit `0192258…`, build `2026-08-04T14:56:46Z` | web public vivant, SQLite ouvrable, dernier commit déployé | worker, connecteurs, données et sauvegardes |
| `/` | `303 /login` | auth activée | dashboard utilisable |
| `/login` | `200`, 1 027 octets | écran de connexion disponible | compte fonctionnel ou parcours post-login |
| Administration | `303 /login` | route protégée déployée | contenu, health providers, actions |
| Data Hub (`/api/data-hub`) | `303 /login` | API protégée | sources `CONNECTED/FRESH`, imports ou volumes |
| Cockpit (`/cockpit`) | `303 /login` | protection globale; cette route n'est pas une route métier canonique identifiée | cockpit métier fonctionnel |
| Roadmap | `303 /login` | route protégée | valeur rendue en production |
| Workers (`/workers`) | `303 /login` | protection globale | existence/heartbeat du worker; il n'existe pas de page `/workers` dédiée dans le routeur |

**Dernières PR mergées observables :** #129 corrige les fuites de descripteurs SQLite; #128 est l'audit antérieur; #127 livre les fondations CRM/fidélité; #126 le cockpit opérations marketing; #125 l'intelligence sociale/stock; #121–124 portent surtout sur Qonto. Le `/health` au SHA #129 et son `build_date` démontrent un déploiement post-merge de ce commit. Les conclusions exactes des workflows GitHub ne sont pas vérifiées.

## 4. Audit de chaque grand module

### 4.1 Core Platform — **75 %**

* **Justification :** architecture réellement déployée, domaine/repositories/services, jobs reprenables, idempotence, événements/audits et configuration runtime. Health et SHA exact vérifiés.
* **Forces :** séparation métier utile; migrations additives; invariants transactionnels; safe mode; volume partagé web/worker.
* **Faiblesses :** composition/routage monolithique; SQLite; notifications et configuration administrative incomplètes; pas d'API contractuelle versionnée.
* **Blocages :** preuve d'exploitation soutenue et cible de scalabilité absentes.
* **Étapes :** découper le routeur, publier OpenAPI, définir SLO, plan PostgreSQL sans migration prématurée.

### 4.2 Administration — **50 %**

* **Justification :** écran et API couvrent état systèmes, Data Hub, diagnostics, imports médias, réhydratation et sauvegardes. Fonctionnement local testé; écran privé non inspecté en production.
* **Forces :** actions sensibles séparées preview/apply; statuts techniques centralisés; erreurs assainies.
* **Faiblesses :** pas de gestion complète de configuration, vue workers dédiée, métriques ou historique de déploiements.
* **Blocages :** session et données production.
* **Étapes :** tableau worker/jobs, historique release/backup, réglages auditables, test E2E authentifié.

### 4.3 Data Hub — **50 %**

* **Justification :** registre provider-neutral, états/fraîcheur, diagnostics, curseurs, jobs et UI existent. PrestaShop, ShopCaisse, Qonto et SumUp ont du code réel, mais aucun état `FRESH` ni volume production n'est observable.
* **Forces :** connectivité distincte de fraîcheur; erreurs catégorisées; diagnostics persistés; handlers worker.
* **Faiblesses :** PrestaShop catalogue peut être déclaré connecté par configuration; ventes ShopCaisse reposent sur CSV inbox; fournisseurs et social non configurés.
* **Blocages :** credentials/health/imports production; endpoint de tickets ShopCaisse non attesté.
* **Étapes :** preuve runtime signée pour chaque source, compteurs/min-max, alertes de fraîcheur, contrats sandbox.

### 4.4 Finance — **50 %**

* **Justification :** écran et APIs summary/cashflow/tax/profitability/reconciliation; catégorisation Qonto et calculs locaux testés. TVA déductible et couverture bancaire complètes absentes.
* **Forces :** montants `Decimal`, catégories explicites, revue humaine, séparation indisponible/zéro.
* **Faiblesses :** pas de comptabilité générale, clôture, export comptable, multi-devise ou validation fiscale.
* **Blocages :** flux Qonto frais et classification métier de production.
* **Étapes :** backfill borné, règles documentées/versionnées, clôture mensuelle et export expert-comptable.

### 4.5 Settlements — **50 %**

* **Justification :** ledger de rapprochement, matching, anomalies, conflits, backfill/recompute et tests existent. Les chaînes ShopCaisse → SumUp → Qonto ne sont pas validées sur production.
* **Forces :** provenance, confiance, décisions humaines, calculs réexécutables et idempotents.
* **Faiblesses :** maillons partiels, mappings paiement `UNKNOWN`, argent réellement en transit inconnu.
* **Blocages :** données réelles simultanées des trois sources.
* **Étapes :** jeu réel anonymisé, seuils métier, rapprochement quotidien supervisé et procédure d'exception.

### 4.6 Cash Cockpit — **50 %**

* **Justification :** synthèses de trésorerie/settlement et indicateurs existent localement; aucune preuve que les cartes sont alimentées en production.
* **Forces :** états indisponibles explicites; fenêtres temporelles et anomalies.
* **Faiblesses :** pas de prévision validée, budget, alertes ou workflow de clôture.
* **Blocages :** Data Hub bancaire/settlement frais.
* **Étapes :** rendre la couverture source visible, réconcilier soldes, alertes quotidiennes, validation DAF.

### 4.7 Explorer — **50 %**

* **Justification :** page settlement explorer, pagination, filtres, cartes mobile et actions de revue sont implémentés.
* **Forces :** recherche multi-critères; explicabilité des maillons/confiance.
* **Faiblesses :** une colonne est explicitement « Indisponible »; aucun parcours navigateur ni volume réel testé.
* **Blocages :** datasets production couvrant conflits et gros volumes.
* **Étapes :** E2E navigateur, export, drill-down source, performance sur historique réel.

### 4.8 Ledger — **50 %**

* **Justification :** ledgers ventes, paiements, stock, coûts et settlements sont persistés et testés, mais ne forment pas un grand livre comptable complet.
* **Forces :** append-only/compensation, idempotence et provenance.
* **Faiblesses :** frontières de ledger multiples; pas de double entrée, clôture, rétention légale ou rapprochement exhaustif.
* **Blocages :** modèle comptable cible et validation expert-comptable.
* **Étapes :** dictionnaire d'autorités, contrôles d'intégrité périodiques, export et clôture.

### 4.9 CRM — **50 %**

* **Justification :** Customer 360, résolution d'identité, consentement, segments, fidélité et campagnes ont modèles, API, UI et tests locaux; livraison très récente et sans validation production.
* **Forces :** consent states, provenance, fusion, métriques et fidélité bornée.
* **Faiblesses :** pas de connecteur emailing/SMS, self-service RGPD, délivrabilité, suppression automatisée ou campagne réelle.
* **Blocages :** données clients réelles, base légale et validation DPO.
* **Étapes :** audit privacy, import contrôlé, droits d'accès/suppression, pilote fidélité mesuré.

### 4.10 Marketing — **50 %**

* **Justification :** cockpit, calendrier, revue, campagnes, propositions, scheduling interne, analytics et learning loop sont fonctionnels localement.
* **Forces :** validation humaine; liens stock/vente; états et journalisation.
* **Faiblesses :** boutons d'opérations avancées encore désactivés sur certaines vues; attribution limitée; aucune publication externe.
* **Blocages :** providers sociaux et données analytics réelles.
* **Étapes :** terminer les workflows UI, KPI validés, campagne pilote sans publication automatique puis A/B testing.

### 4.11 Creative AI — **25 %**

* **Justification :** génération/revue de créatifs, variantes et garde-fous existent comme fondations; aucun modèle IA externe, évaluation qualité ou usage production n'est prouvé.
* **Forces :** contrats, revue humaine obligatoire, médias locaux et audit.
* **Faiblesses :** « IA » surtout déterministe/provider-neutral; pas de coût, prompt/version, sécurité modèle ou benchmark.
* **Blocages :** provider homologué et politique de données.
* **Étapes :** adapter réel en sandbox, évaluation éditoriale, suivi coûts/latence, red-team et pilote humain.

### 4.12 Social — **25 %**

* **Justification :** modèles connexions, queue, analytics et UI sont prêts, mais Meta/Instagram/TikTok/Snapchat sont enregistrés `NOT_CONFIGURED`; le publisher lève `NotImplementedError`.
* **Forces :** aucun faux envoi; statuts transparents; planification interne.
* **Faiblesses :** absence OAuth, webhooks, refresh tokens, publication et collecte analytics réelles.
* **Blocages :** comptes/app reviews et APIs fournisseurs.
* **Étapes :** commencer par un canal, health OAuth, publication brouillon, webhook, révocation et métriques.

### 4.13 Automation / IA — **50 %**

* **Justification :** service `automation-worker`, scheduler, heartbeats, locks, retries et handlers Data Hub existent et sont déployables; `/health` ne valide pas le worker et son heartbeat production est inaccessible.
* **Forces :** reprise/idempotence, cadences configurables, partage DB, healthcheck Compose.
* **Faiblesses :** processus unique, pas de DLQ, métriques/alertes; peu d'IA réelle.
* **Blocages :** preuve heartbeat/jobs récents en production.
* **Étapes :** vue worker, alerte stale/failed, DLQ/replay, budgets et tests de panne.

### 4.14 Catalogue — **50 %**

* **Justification :** catalogue central, recherche, filtres, mapping, EAN, médias et identité stable sont très complets localement. Le snapshot versionné ne prouve pas la fraîcheur production.
* **Forces :** `drcloud_product_key`, qualité, variantes, mapping historique 478/478 et écritures externes désactivées par défaut.
* **Faiblesses :** autorité partagée/historique complexe; PostgreSQL absent; validation terrain inconnue.
* **Blocages :** sync fraîche, audit physique et politique de cycle de vie.
* **Étapes :** rapport qualité production, delta quotidien, échantillon terrain, archivage produits.

### 4.15 Produits — **50 %**

* **Justification :** fiches commerciales, coûts, EAN, images et variantes sont éditables localement avec tests.
* **Forces :** identité stable, validations et provenance média.
* **Faiblesses :** PIM incomplet : catégories, traductions, règles de prix, workflow publication et bulk edit limités.
* **Blocages :** gouvernance produit et writes connecteurs homologués.
* **Étapes :** schéma de complétude, workflow brouillon/validé, import/export et synchro contrôlée.

### 4.16 Réhydratation — **50 %**

* **Justification :** status/preview/report/apply, classifications SAFE/AMBIGUOUS/NO_DATA, backup préalable et UI sont testés.
* **Forces :** aucun écrasement aveugle; choix humain; traçabilité et reprise média.
* **Faiblesses :** dépend d'artefacts historiques et chemins locaux; exercice production non observé.
* **Blocages :** backup restaurable et rapport réel validé.
* **Étapes :** dry-run production, approbation à quatre yeux, restauration testée, rapport après application.

### 4.17 Stock — **50 %**

* **Justification :** inventaire, scan, propositions, validation/apply, ledger, projection et comparaison PrestaShop existent localement.
* **Forces :** mouvements compensatoires, idempotence, observations fraîches/stales et safe mode.
* **Faiblesses :** ShopCaisse non observable, sync write live désactivée, seuils/alertes et tests terrain EAN absents.
* **Blocages :** connecteurs d'écriture homologués et inventaire physique.
* **Étapes :** pilote magasin, alertes, divergence multi-source, validation write par petits lots.

### 4.18 Purchases — **50 %**

* **Justification :** fournisseurs, commandes, lignes, réceptions, mouvements stock, coûts, factures preview/apply et écrans sont fonctionnels localement.
* **Forces :** états métier, contrôle humain, coûts par lots et impacts stock.
* **Faiblesses :** aucun fournisseur/OCR réel; commandes non envoyées; TVA et rapprochement facture incomplets en exploitation.
* **Blocages :** documents et fournisseurs réels.
* **Étapes :** pilote fournisseur, import facture, three-way match, écarts prix/TVA, export comptable.

### 4.19 Sécurité — **75 %**

* **Justification :** sessions persistées/révocables, RBAC fail-closed, CSRF, mots de passe hashés, audit, gestion utilisateurs/secrets et headers existent; production redirige correctement vers login.
* **Forces :** permissions par domaine, erreurs sans trace/secret, compte admin protégé, non-root et secrets hors dépôt.
* **Faiblesses :** pas de MFA/SSO, rate-limit distribué, scanner SAST/dependencies/containers, pentest ou SIEM démontré.
* **Blocages :** revue indépendante et preuves d'exploitation.
* **Étapes :** MFA, rotation/récupération, scans CI, pentest, alertes auth et revue trimestrielle RBAC.

### 4.20 Observabilité — **50 %**

* **Justification :** `/health`, request IDs, ActivityLog, diagnostics connecteurs, jobs, fraîcheur, erreurs et heartbeat existent. Aucun Prometheus/Grafana/Sentry/OpenTelemetry ni pager n'est configuré.
* **Forces :** signal métier structuré; statuts non trompeurs; diagnostics assainis.
* **Faiblesses :** logs conteneur seulement, pas de métriques temporelles, traces, rétention ni alertes externes.
* **Blocages :** stack et SLO opérationnels.
* **Étapes :** métriques RED, logs centralisés, dashboards/SLO, alertes backup/worker/connecteurs/disque.

### 4.21 CI/CD — **75 %**

* **Justification :** CI sur PR/push teste Python, compile, valide JS/shell/Compose, build l'image; production ne suit qu'une CI `main` réussie et déploie le SHA exact. Le SHA public correspond.
* **Forces :** concurrence contrôlée, checkout SHA, image inspectée, déploiement automatisé et rollback code.
* **Faiblesses :** pas de coverage gate, lint/typecheck, scan sécurité/SBOM, staging, migration rehearsal ou smoke authentifié.
* **Blocages :** visibilité Actions non disponible dans l'audit.
* **Étapes :** quality/security gates, artefact immuable, staging, smoke privé read-only et rapport de release.

### 4.22 Déploiement — **75 %**

* **Justification :** Docker non-root, Compose web+worker, Caddy, volume, healthchecks, scripts OVH, backup avant update et rollback. Production sert le commit attendu.
* **Forces :** atomicité raisonnable, contrôles local/public, secrets mode `0600`, aucun reset destructif.
* **Faiblesses :** VPS unique/SQLite unique, domaine documentaire divergent, pas de HA/DR mesuré ni déploiement sans interruption.
* **Blocages :** RPO/RTO et restauration observée.
* **Étapes :** corriger domaine canonique, exercice restore, monitoring hôte, runbook incident et capacité.

### 4.23 Documentation — **75 %**

* **Justification :** plus de 70 documents couvrent architecture, ADR, domaines, connecteurs, sécurité, exploitation et restauration.
* **Forces :** décisions et limites souvent explicites; runbooks présents.
* **Faiblesses :** répétitions et contradictions; roadmap binaire gonflée; README encore centré « Sync v1 »; assertions datées non automatiquement vérifiées.
* **Blocages :** propriétaire/revue et source unique de vérité.
* **Étapes :** index, statut/date/owner, tests de liens, archivage, synchronisation contractuelle code-doc.

### 4.24 Tests — **50 %**

* **Justification :** **514 tests passent**, 1 est ignoré; couverture fonctionnelle locale exceptionnellement large. Le niveau 75 est refusé faute d'E2E production observable, couverture chiffrée et tests réels réguliers de connecteurs/restore.
* **Forces :** sécurité, migrations, finance, CRM, marketing, stock, achats, settlements, backups et frontend assets testés.
* **Faiblesses :** mocks nombreux, pas de navigateur, performance, chaos, fuzzing, mutation ou test de charge; un test optionnel ignoré.
* **Blocages :** environnements sandbox et fixtures production anonymisées.
* **Étapes :** coverage/branch baseline, Playwright, contrats providers, charge SQLite, restore automatisé et non-régression production read-only.

## 5. Roadmap recalculée

Les poids ci-dessous sont recalculés pour totaliser **100**. « Ancienne valeur » est la projection actuelle du manifeste pour le domaine équivalent; `n/a` indique qu'il n'existe pas comme module autonome. L'écart n'est calculé que lorsqu'une ancienne valeur comparable existe.

| Module | Poids | Ancienne valeur | Nouvelle valeur | Écart | Justification courte |
|---|---:|---:|---:|---:|---|
| Core Platform | 7 | 76,92 % | 75 % | -1,92 | déployé et observable, architecture solide |
| Administration | 5 | n/a | 50 % | n/a | cockpit local, production privée non vérifiée |
| Data Hub | 6 | n/a | 50 % | n/a | runtime réel, fraîcheur production inconnue |
| Finance | 5 | 81,82 % | 50 % | -31,82 | calculs locaux sans clôture/usage réel |
| Settlements | 5 | n/a | 50 % | n/a | ledger complet local, chaîne réelle inconnue |
| Cash Cockpit | 4 | n/a | 50 % | n/a | indicateurs non validés par données fraîches |
| Explorer | 3 | n/a | 50 % | n/a | UI/API locales, pas de volume production |
| Ledger | 5 | n/a | 50 % | n/a | ledgers métier, pas grand livre complet |
| CRM | 4 | 60,00 % | 50 % | -10,00 | V1 locale récente, aucun canal réel |
| Marketing | 4 | 78,57 % | 50 % | -28,57 | cockpit local, usage production inconnu |
| Creative AI | 3 | 5,00 %* | 25 % | +20,00 | fondations réelles, aucun modèle réel |
| Social | 3 | 78,57 %* | 25 % | -53,57 | providers explicitement non configurés |
| Automation / IA | 5 | 5,00 % | 50 % | +45,00 | vrai worker/jobs sous-représentés |
| Catalogue | 5 | 90,00 % | 50 % | -40,00 | très fonctionnel localement, fraîcheur inconnue |
| Produits | 4 | 90,00 %* | 50 % | -40,00 | fiche locale, PIM/connecteurs incomplets |
| Réhydratation | 3 | n/a | 50 % | n/a | workflow sûr et testé, prod non exercée |
| Stock | 5 | 90,00 % | 50 % | -40,00 | ledger local, write live/terrain absents |
| Purchases | 5 | 100,00 % | 50 % | -50,00 | parcours local, aucun fournisseur réel |
| Sécurité | 5 | 100,00 % | 75 % | -25,00 | déployé/observable, pas validation complète |
| Observabilité | 4 | n/a | 50 % | n/a | diagnostics internes, pas de stack/alerting |
| CI/CD | 4 | 78,95 %* | 75 % | -3,95 | pipeline et SHA déployé vérifiés |
| Déploiement | 4 | 78,95 %* | 75 % | -3,95 | prod saine, restauration/HA non prouvées |
| Documentation | 1 | n/a | 75 % | n/a | riche, mais contradictions et dérive |
| Tests | 1 | n/a | 50 % | n/a | 514 succès, pas E2E/coverage/contrats réels |

`*` Ancienne valeur issue d'un module agrégé, donc comparaison indicative seulement.
**Total pondéré recalculé : 53,75 %, arrondi officiellement à 54 %.**

## 6. Scores synthétiques

| Score | Valeur | Calcul/interprétation |
|---|---:|---|
| **Global réel** | **54/100** | moyenne pondérée des 24 modules |
| **Production** | **50/100** | application et SHA déployés, mais écrans privés/connecteurs/usage non validés; sécurité/déploiement empêchent une note plus basse |
| **Technique** | **65/100** | moyenne experte des fondations, architecture, sécurité, CI/CD, déploiement, observabilité et tests; forte base mais exploitation incomplète |
| **Métier** | **48/100** | parcours locaux nombreux, presque aucun usage complet attesté en production |
| **Intégrations** | **38/100** | adaptateurs réels PrestaShop/ShopCaisse/Qonto/SumUp, mais santé/import production inconnus et social/fournisseurs absents |

Ces quatre sous-scores sont des axes de lecture, pas une seconde moyenne du global. Ils évitent qu'un excellent pipeline masque l'absence de flux métier réels.

## 7. Classements

### 7.1 Top 10 des fonctionnalités les plus matures

1. Health public et identification exacte de build.
2. Pipeline CI → déploiement du SHA `main` réussi.
3. Authentification, sessions révocables, RBAC et CSRF.
4. Schéma/migrations SQLite additives et intégrité locale.
5. Catalogue central et identité produit stable.
6. Inventaire avec scan, comptage, persistance et export.
7. StockMovement append-only, idempotence et projection.
8. Achats : fournisseurs, commandes et réceptions locales.
9. Jobs reprenables, locks et diagnostics Data Hub.
10. Sauvegarde avant déploiement et rollback du code.

### 7.2 Top 10 des fonctionnalités les moins matures

1. Publication réelle Instagram/Facebook/TikTok/Snapchat.
2. Creative AI reliée à un modèle évalué et supervisé.
3. Connecteurs fournisseurs et OCR de factures réels.
4. Écriture de stock live homologuée vers systèmes externes.
5. TVA/comptabilité/clôture et export expert-comptable complets.
6. CRM emailing/SMS et campagnes réellement envoyées.
7. Observabilité externe avec métriques, SLO et alerting.
8. Haute disponibilité et reprise après sinistre testée.
9. E2E navigateur et tests contractuels périodiques fournisseurs.
10. Validation métier de la chaîne settlements complète sur données production.

## 8. Top 20 priorités de développement

| Rang | Priorité | Résultat vérifiable attendu |
|---:|---|---|
| 1 | P0 — preuve Data Hub production | chaque source affiche health, dernier succès, lignes, min/max et fraîcheur |
| 2 | P0 — worker observable | heartbeat, jobs en retard/échec et alerte externe |
| 3 | P0 — sauvegarde/restauration | exercice horodaté avec RPO/RTO et contrôle fonctionnel |
| 4 | P0 — corriger roadmap | remplacer DONE binaire par niveaux de preuve du présent audit |
| 5 | P0 — monitoring | métriques, logs centralisés, dashboards et alertes health/disque/backup |
| 6 | P0 — settlement réel | lot anonymisé ShopCaisse/SumUp/Qonto rapproché et signé métier |
| 7 | P0 — Qonto | health/import durable et procédure WAF/401 validés |
| 8 | P1 — E2E authentifié | Playwright sur login, admin, Data Hub, stock, finance, CRM |
| 9 | P1 — qualité CI | coverage, lint, types, dependency/container scan et SBOM |
| 10 | P1 — inventaire terrain | pilote EAN, écarts, corrections et temps opérateur mesurés |
| 11 | P1 — finance | clôture mensuelle, règles versionnées et export comptable |
| 12 | P1 — achats | pilote facture/réception et three-way matching |
| 13 | P1 — CRM privacy | base légale, droits RGPD, rétention et revue DPO |
| 14 | P1 — social canal unique | OAuth, draft publish, webhook, révocation et analytics sandbox |
| 15 | P1 — alertes stock | seuils métier et workflow de traitement |
| 16 | P1 — contrats API | OpenAPI/versionnement/validation et tests de compatibilité |
| 17 | P2 — découper WSGI | contrôleurs par domaine et composition testable |
| 18 | P2 — stratégie données | seuils de bascule SQLite/PostgreSQL, migration et rollback testés |
| 19 | P2 — Creative AI | provider sandbox, benchmark, coût/latence, sécurité et revue humaine |
| 20 | P2 — gouvernance docs | index, owners, dates de revue, liens testés et archivage |

## 9. Incohérences et sur/sous-évaluations

1. **Roadmap 75,93 % vs maturité 54 % : +21,93 points.** Elle mesure 118 jalons `DONE` sur 159, pas les niveaux fondation/local/déployé/production validée.
2. **Achats 100 % vs 50 %.** Le parcours local est excellent, mais aucun connecteur fournisseur, document réel ou usage complet n'est prouvé.
3. **Sécurité 100 % vs 75 %.** Solide et déployée, mais sans MFA, pentest, scans/monitoring complets ni validation production à 100 %.
4. **Catalogue/Stock 90 % vs 50 %.** Fonctionnalités locales riches; write live, fraîcheur connecteurs et validation terrain manquent.
5. **Finance 81,82 % vs 50 %.** Le cockpit n'est ni une comptabilité complète ni une clôture fiscale validée.
6. **Marketing/Social agrégés à 78,57 %.** Marketing local mérite 50 %, social seulement 25 % car tous les providers restent non configurés.
7. **Automation affichée 5 % vs 50 %.** C'est la principale sous-évaluation : worker, jobs, locks, retries et heartbeats existent réellement.
8. **Creative AI est agrégée à Automation.** Ses fondations valent 25 %, mais aucune IA externe validée ne justifie davantage.
9. **Production/PWA 78,95 %.** La partie déploiement vaut 75 %, mais ce pourcentage ne garantit ni worker, sauvegarde restaurable ni connecteurs.
10. **`/workers` n'est pas un écran métier identifié.** La redirection générique ne doit pas être interprétée comme une page Workers disponible.
11. **`/cockpit` n'est pas le cockpit canonique.** Les cockpits réels sont répartis sous dashboard/finance/settlements/marketing/CRM.
12. **Domaines divergents :** scripts et production utilisent `osdrcloud.fr`, tandis que la documentation opérateur mentionne aussi `os.drcloud.fr`, qui répond 503 lors de l'audit.
13. **`CONNECTED` n'implique pas `FRESH`.** Un health ou une configuration peut exister sans aucune ligne importée; l'UI et la roadmap doivent conserver cette distinction.
14. **PrestaShop « connecté » par présence de configuration.** Ce niveau est inférieur à un health + sync durable observés.
15. **ShopCaisse ventes.** L'API catalogue existe, mais les ventes reposent explicitement sur CSV/inbox; ne pas annoncer un connecteur live tickets.
16. **Qonto « réel ».** L'adaptateur et l'injection de secret sont réels, mais les PR récentes WAF/401 et l'absence de Data Hub authentifié interdisent « opérationnel ».
17. **Settlements/cash.** Les écrans peuvent être complets tout en restant vides ou partiels si un maillon source manque.
18. **CRM « livré ».** La livraison code #127 est réelle, l'usage fidélité et conformité sur clients réels ne le sont pas encore.
19. **Tests très nombreux, maturité tests seulement 50 %.** Le barème impose 75 % seulement lorsque la dimension est déployée/utilisable/observable; la suite reste locale et sans E2E/coverage.
20. **Sauvegarde présente vs résilience validée.** Créer un fichier n'est pas restaurer un service; aucune preuve d'exercice n'est disponible.

## 10. Décision de roadmap officielle

La roadmap officielle doit désormais publier **54 % de maturité globale**, accompagnée des axes **production 50, technique 65, métier 48, intégrations 38**. Le chiffre 75,93 % peut rester comme indicateur séparé de « jalons de code déclarés terminés », mais ne doit plus être libellé avancement réel.

### Conditions minimales pour dépasser 54 %

* Une preuve production authentifiée et assainie des pages Administration/Data Hub/cockpits et du worker.
* Au moins un cycle complet de données réelles pour catalogue/ventes/banque/settlements avec fraîcheur, compteurs et revue métier.
* Monitoring/alertes et restauration testée.
* E2E navigateur et contrats connecteurs réguliers.
* Déclassement automatique lorsqu'un provider, job ou donnée devient stale/error.

### Conditions d'un module à 100 %

Aucun des modules audités n'atteint 100 % aujourd'hui. Pour y parvenir, il faut simultanément : production validée, connecteurs réels concernés, tests locaux/E2E/contrats, monitoring/alertes et utilisation métier complète démontrée. Une architecture excellente ou un écran complet ne suffit pas — mais constitue une fondation explicitement reconnue plutôt qu'une pénalité.

## Annexe A — commandes de contrôle

```bash
git status --short --branch
git log --oneline --decorate -20
git log --merges -10 --format='%h|%cI|%s'
rg --files -g '!dist/**'
wc -l src/dr_cloud_sync/*.py src/dr_cloud_sync/static/*.{js,html,css} tests/test_*.py
PYTHONPATH=src python -c 'from pathlib import Path; from dr_cloud_sync.roadmap import RoadmapService; print(RoadmapService(Path("docs/drcloud-os-roadmap.json")).load())'
python -m pytest -q
curl -sS --max-time 15 https://osdrcloud.fr/health
curl -sS -D - --max-time 15 https://osdrcloud.fr/{login,administration,api/data-hub,cockpit,roadmap,workers}
curl -sS --max-time 15 https://os.drcloud.fr/health
```

## Annexe B — verdict

DrCloud OS n'est ni un prototype vide ni une plateforme achevée. C'est un produit **techniquement solide, réellement déployé et très riche localement**, dont le principal déficit n'est plus l'architecture mais la **preuve d'exploitation** : données fraîches, connecteurs confirmés, observabilité externe, restauration, E2E et adoption métier. La note de **54/100** reconnaît les fondations sans transformer des capacités locales en succès production.
