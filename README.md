# Dr Cloud Sync v1

Dr Cloud Sync construit un snapshot local du catalogue dont **PrestaShop est la source maître**.
La v1 lit le Webservice de `dr-cloudshop.com` et conserve les réponses JSON complètes pour :

- `products` (dont référence et EAN produit) ;
- `combinations` (dont référence, EAN et associations d'attributs) ;
- `product_options` et `product_option_values` (groupes et valeurs d'attributs) ;
- `stock_availables` (quantités produit/déclinaison).

Un connecteur ShopCaisse en lecture seule est également disponible. Le workflow manuel
`Pull catalogue ShopCaisse` vérifie d'abord le secret GitHub `SHOPCAISSE_API_KEY`, teste
l'authentification, pagine le catalogue, puis publie les snapshots et le rapport de
rapprochement comme artefact sans les committer.
Le programme ne pousse aucune donnée vers PrestaShop et la clé recommandée doit disposer uniquement de droits `GET`.

## Inventaire DRCloud V1 (local et sans écriture distante)

Une interface mobile-first de comptage est disponible à partir des artefacts du mapping final validé. Elle utilise
SQLite pour sauvegarder immédiatement la session, les quantités (y compris zéro) et le journal d'audit. Aucun client
PrestaShop ou ShopCaisse n'est instancié par ce serveur : les comparaisons et exports sont exclusivement locaux.

```bash
dr-cloud-sync inventory-serve
# ouvrir http://127.0.0.1:8080
```

Pour un accès depuis le réseau local, définir `INVENTORY_HOST=0.0.0.0` et protéger l'accès au niveau du serveur/réseau.
Les variables `INVENTORY_CATALOGUE`, `INVENTORY_MAPPING_REPORT`, `INVENTORY_DATABASE` et `INVENTORY_PORT` permettent
de changer les chemins et le port. L'interface propose douchette/saisie EAN, caméra lorsque `BarcodeDetector` est
supporté, recherche, modes Quantité et +1, listes de progression, corrections avec historique et exports JSON/CSV.

## Installation et configuration

Python 3.11 ou plus récent est requis.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
```

Le programme ne charge pas `.env` automatiquement afin d'éviter une dépendance et toute ambiguïté de déploiement.
Exportez les variables avec votre gestionnaire de secrets ou, localement, avec `set -a; . ./.env; set +a`.
`PRESTASHOP_API_KEY` est obligatoire et ne doit jamais être commise. `.env` et les bases locales sont ignorés par Git.

## Récupérer le catalogue

```bash
export PRESTASHOP_API_KEY='cle-webservice-lecture-seule'
dr-cloud-sync pull
```

Chaque ressource est paginée. Le snapshot n'est remplacé dans SQLite qu'après la récupération réussie de toutes les
ressources ; un échec conserve donc le dernier snapshot complet. La table `sync_runs` donne l'état et les compteurs de
chaque exécution, tandis que `prestashop_entities` conserve une ligne par ressource et identifiant source.

## Contrôle de la connexion réelle

La commande suivante contacte réellement le domaine et nécessite une clé fournie hors Git :

```bash
PRESTASHOP_API_KEY="$PRESTASHOP_API_KEY" dr-cloud-sync pull
```

Le client utilise HTTPS, l'authentification Basic prescrite par le Webservice (clé en nom d'utilisateur), des délais
d'attente, une pagination et de nouvelles tentatives sur les erreurs transitoires. Les erreurs affichées ne contiennent
jamais la clé.

## Récupération ShopCaisse

La wheel contient le snapshot historique versionné sous `dr_cloud_sync/data/catalogue-prestashop-reconstruit.json`. Le pull local se lance avec :

```bash
SHOPCAISSE_API_KEY="$SHOPCAISSE_API_KEY" dr-cloud-sync shopcaisse-pull
```

La commande utilise exclusivement `https://api.shop-caisse.com/v1`, n'effectue que des requêtes `GET` et produit
`catalogue-shopcaisse-brut.json`, `catalogue-shopcaisse-normalise.json` et
`rapport-shopcaisse-prestashop.json` dans `dist/`. Le rapprochement privilégie l'EAN, puis le SKU, le libellé avec sa
déclinaison, et enfin une similarité textuelle avec un seuil strict.

## Simulation d'import PrestaShop vers ShopCaisse

```bash
SHOPCAISSE_API_KEY="$SHOPCAISSE_API_KEY" PRESTASHOP_API_KEY="$PRESTASHOP_API_KEY" \
  dr-cloud-sync shopcaisse-import-dry-run
```

Cette commande ne possède que des clients réseau `GET`. Elle demande à PrestaShop de calculer
le prix final TTC avec ses propres règles fiscales et l'impact de chaque déclinaison, conserve
les références exactes disponibles, puis produit localement le plan, les payloads non envoyés
et le rapport dans `dist/`. Les payloads sont validés contre `CreateSimpleItemDto`; une création
incomplète devient `DONNEES_MANQUANTES`, jamais `PRET_A_CREER`. Le schéma ShopCaisse public ne
propose ni écriture de stock, ni création explicite de variante ou de relation parent/enfant :
une simulation d'article simple nommé « produit - attributs » est donc préparée par déclinaison.

## DrCloud OS — Catalogue et Inventaire V2

L'inventaire est désormais un module de l'interface unique **DrCloud OS**, avec un catalogue central fondé sur la clé déterministe `drc:<prestashop_key>`. Les types métier sont dans `domain.py`, les ports et l'adaptateur SQLite dans `repositories.py`, les cas d'usage dans `services.py`, et les contrats d'intégration dans `connectors.py`. Le métier ne dépend ainsi ni de SQLite, ni de HTTP, ni d'un fournisseur.

`BARCODE_SYNC_MODE` vaut `dry-run` par défaut : la validation, la confirmation et les payloads sont exécutés localement, sans aucune requête distante. Le mode `live` est structurellement prévu mais ses connecteurs restent explicitement désactivés tant que les méthodes d'écriture ShopCaisse et PrestaShop n'ont pas été validées. Le catalogue et l'inventaire sont accessibles dans la même navigation ; Stocks, Achats (dont les futures réceptions fournisseurs) et Réseaux sociaux restent des emplacements réservés.

Le [plan directeur DrCloud OS](docs/DRCLOUD_OS_ARCHITECTURE.md), la
[roadmap officielle](docs/DRCLOUD_OS_ROADMAP.md) et ses décisions dans `docs/adr/`
définissent les frontières du monolithe modulaire. La page **Roadmap** de DrCloud OS
lit `docs/drcloud-os-roadmap.json` par l'intermédiaire de `RoadmapService` : les
pourcentages affichés sont recalculés depuis les jalons, et non inscrits dans la vue.

## Stock movement ledger

`StockMovement` in `dr_cloud_sync.domain` is the single stock movement contract. New
entries are append-only and start as `PENDING`: this foundation does not update a
stock projection or call a remote system. A correction must therefore be represented
by a later compensating movement, never by rewriting an existing business payload.

Idempotence is scoped to `(source_type, idempotency_key)`. Producers must provide a
stable key for one logical operation (inventory proposals use
`<inventory source_id>:<drcloud_product_key>`). An exact replay returns the stored
movement with `created=False`; reuse for a different product, delta, movement type or
source reference raises `StockMovementConflict`. SQLite enforces this contract with a
partial unique index. Legacy rows receive the isolated `LEGACY` scope and a key
derived from their already-unique technical id.

At startup, schema setup is additive and idempotent. Existing `stock_movements` rows
and their `prestashop_key` are preserved, `drcloud_product_key` is backfilled as
`drc:<prestashop_key>`, and the ledger/audit columns and indexes are added. Legacy
rows receive `legacy:<existing id>` so they are readable through the canonical model
without conflating any historical operations.

## Resumable synchronization jobs

`sync_runs` is the canonical job history. `JobRun` adds a stable UUID (or a caller-supplied
identity), job/connector/operation, bounded attempts, timestamps, sanitized error metadata and
a small JSON summary. Its transitions are `PENDING → RUNNING → SUCCEEDED` and
`PENDING/FAILED/RETRYABLE → RUNNING → FAILED/RETRYABLE`; `SUCCEEDED` and `RUNNING` cannot be
acquired again. The atomic conditional SQLite update to `RUNNING` is the execution lock and
increments `attempt`. A retry keeps the same `job_id`; optional unique
`(job_type, idempotency_key)` identifies a replay, while omitting the key creates a deliberately
new synchronization.

Schema migration is additive and repeatable. Existing `sync_runs` columns and rows are retained;
their technical id becomes `legacy-sync-<id>`, `started_at` becomes `created_at`, and lowercase
`running`, `completed`, and `failed` are read respectively as `RUNNING`, `SUCCEEDED`, and
`FAILED`. No legacy snapshot or history is reset.

The PrestaShop snapshot pull now runs through `JobRunner`. Every HTTP page is still fetched
before the snapshot transaction begins, so a remote or validation failure leaves the prior
snapshot intact. A recoverable job can repeat that intrinsically safe full read/atomic replace
up to `max_attempts`; a completed job replay returns its persisted summary without another HTTP
call. ShopCaisse clients already have bounded sanitized HTTP retries, but their multiple manual
read/import workflows are intentionally not migrated here: the generic connector/operation job
contract can host a later, separately scoped migration without changing safe-mode permissions.

## Observations externes de stock (lecture seule)

La projection des mouvements `APPLIED` du ledger reste l'unique stock local DrCloud OS.
Chaque pull PrestaShop complet conserve, dans la même transaction que le snapshot, les
quantités `stock_availables`, leur date et le `job_id`. La comparaison n'utilise que la
dernière observation dont le job persistant est `SUCCEEDED`; un échec ou un snapshot
partiel laisse donc la précédente observation valide disponible. L'identité repose
exclusivement sur `(product_id, combination_id)` puis sur la clé DrCloud mappée.

Une observation est `FRESH` pendant 24 heures (seuil centralisé dans
`external_stock.DEFAULT_STALE_AFTER`), puis `STALE`. Les états de comparaison sont
`MATCH`, `DIFFERENCE`, `STALE`, `UNKNOWN` et `INCONSISTENT`. ShopCaisse reste affiché
comme indisponible : ses exports actuels ne constituent pas un snapshot transactionnel
persistant, daté et relié à un `JobRun`. Les endpoints et la page Stock ne font aucun
appel HTTP et ne créent aucun mouvement ; le rafraîchissement contrôlé reste la commande
read-only existante `dr-cloud-sync pull`.
