# Domaine Ventes v1

## Audit de la base #78

La livraison #78 contenait déjà `SaleEvent`, le Sales Ledger SQLite append-only, sa clé
d'idempotence, le PREVIEW/APPLY CSV, les agrégats 7/30 jours et le port consommé par
Marketing. Ventes v1 réutilise directement ce ledger : il ne crée ni second ledger ni
mouvement de stock.

## Modèle et flux

`CanonicalSale` regroupe source, identifiant externe, instant avec offset, fuseau,
canal, devise, statut, lieu et lignes. `CanonicalSaleLine` conserve les identifiants
source, EAN/référence observés, quantités, prix TTC/HT, taxe nullable et nature
`SALE`, `REFUND`, `RETURN`, `CANCELLATION` ou `ADJUSTMENT`. Les tables `sales` et
`sale_lines` conservent la vue opérationnelle; chaque ligne alimente le `SaleEvent`
existant. Les événements compensatoires sont négatifs dans les analytics, sans
supprimer l'historique.

La clé SHA-256 de `source | external_sale_id | external_line_id | event_kind` et les
contraintes SQLite rendent une resynchronisation idempotente. Un curseur, les dates
de tentative/succès, l'état et une erreur nettoyée sont conservés par source.

## Sources réellement supportées

* **ShopCaisse** : aucun endpoint de ventes vérifié n'existe dans l'intégration du
  dépôt. La v1 n'en invente pas. Elle fournit un port et un import CSV opérateur
  documenté (`sale_id,line_id,sold_at,quantity`, puis champs optionnels `event_kind`,
  `item_id`, `variant_id`, `reference`, `ean`, prix/totaux). Le cockpit impose un
  PREVIEW du contenu exact avant APPLY. L'intégration réseau reste partielle.
* **PrestaShop** : l'adapter utilise exclusivement les ressources Webservice GET
  `orders` et `order_details`. `PRESTASHOP_PAID_STATE_IDS` (injecté à la construction)
  doit lister explicitement les états réellement encaissés : une commande simplement
  créée n'est jamais une vente. Les remboursements ne sont importés que lorsqu'un
  futur adapter pourra les observer de façon fiable; aucune écriture distante n'a
  lieu.

## Rapprochement et intervention humaine

L'ordre est mapping explicite persistant, clé DrCloud, combinaison PrestaShop,
identifiant ShopCaisse, EAN exact, puis référence exacte. Aucun nom et aucun fuzzy
matching ne sont utilisés. Zéro candidat donne `UNMATCHED`; plusieurs candidats
donnent `AMBIGUOUS`; les deux restent stockés et visibles. Une validation opérateur
crée/modifie `sales_product_mappings` et un audit, sans toucher au Catalogue.

## Fraîcheur, sécurité et observabilité

Les états sont `FRESH`, `STALE`, `UNAVAILABLE` ou `ERROR`; le seuil (48 h par défaut)
est configurable. Le cockpit expose dernière réussite, compte importé/non rapproché
et erreur nettoyée. Les pages nécessitent une session; sync, import et mapping passent
par le contrôle CSRF global. Les audits sont agrégés par batch (`SALES_SYNC_*`,
`SALES_IMPORT_*`, `SALES_MAPPING_*`) et ne contiennent aucun credential.

## Frontières et limites

Le flux est uniquement source externe vers DrCloud OS. Aucun adapter de ventes ne
modifie produit, EAN, référence, média, stock, PrestaShop ou ShopCaisse. Une vente
n'engendre jamais `StockMovement`: le Sales Ledger est la vérité commerciale observée,
le Stock Ledger la vérité physique. TTC, HT, taxe et remboursements préparent Finance,
mais aucune marge ou bénéfice n'est inventé sans coût réel.

Pour ajouter une source, implémenter `SalesProvider.fetch(cursor, since)` et retourner
un `ProviderBatch` de ventes canoniques, déclarer sa politique de statut et ses
capacités de remboursement, puis enregistrer le provider dans `SalesSyncService`.
Tester normalisation, curseur, idempotence, erreurs, lecture seule et invariants avant
de l'exposer à l'UI ou à un job. Aucun cron n'est installé par la v1.
