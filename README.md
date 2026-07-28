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

Le dépôt doit déjà contenir `dist/catalogue-prestashop-reconstruit.json`. Le pull local se lance avec :

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
