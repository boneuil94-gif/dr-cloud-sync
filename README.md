# Dr Cloud Sync v1

Dr Cloud Sync construit un snapshot local du catalogue dont **PrestaShop est la source maître**.
La v1 lit le Webservice de `dr-cloudshop.com` et conserve les réponses JSON complètes pour :

- `products` (dont référence et EAN produit) ;
- `combinations` (dont référence, EAN et associations d'attributs) ;
- `product_options` et `product_option_values` (groupes et valeurs d'attributs) ;
- `stock_availables` (quantités produit/déclinaison).

Il n'existe volontairement **aucun connecteur Shop Caisse** : il sera ajouté après validation de son API.
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

