# Sales Ledger V1 — contrat et audit des sources

## Source de vérité et frontière

Le Sales Ledger SQLite local est la source canonique des **observations commerciales
importées**. Il est en lecture seule vis-à-vis de PrestaShop et ShopCaisse. Un import
ne crée jamais de `StockMovement`, ne modifie aucune commande distante et ne fait ni
comptabilité, ni attribution marketing. Les données client sont limitées à une
référence technique facultative.

## Audit réel des connecteurs (PR L)

* Le client PrestaShop existant lit de façon fiable le catalogue (`products`,
  `combinations`, `stock_availables`) et les prix catalogue HT. Il ne possède pas
  encore de contrat de lecture des `orders`, `order_details`, statuts, taxes,
  remises ou remboursements. **Aucune vente PrestaShop n'est donc inventée ni
  importée automatiquement.** Le normaliseur accepte des snapshots validés futurs;
  un statut absent reste `PENDING`, jamais `COMPLETED` implicitement.
* Le client ShopCaisse existant couvre l'API catalogue/items documentée. Aucun
  endpoint de tickets/ventes fiable n'est établi dans le dépôt. La couverture Sales
  ShopCaisse est donc `PARTIAL`/indisponible jusqu'à validation d'un contrat réel.
* Le mapping durable fournit la clé produit DrCloud à partir des références
  externes. Une ligne non rapprochée est conservée avec `UNMAPPED`, mais exclue des
  classements produit.

## Identité, idempotence et anti-double comptage

`sale:<UUIDv5>` dérive de `(source, source_sale_id)` et `saleline:<UUIDv5>` de la
vente et de l'identité de ligne. Une contrainte unique `(source, source_sale_id)` et
une transaction par lot rendent retry/backfill idempotents. Les ventes de deux
sources restent distinctes. Il n'existe actuellement aucun identifiant croisé
déterministe permettant de conclure qu'un ticket ShopCaisse est une commande
PrestaShop; montant, date et panier ne sont jamais utilisés pour dédupliquer. Ce
choix évite les fusions fausses mais laisse un risque de double comptage clairement
signalé tant qu'un contrat de référence croisée n'existe pas.

## Montants, statuts et remboursements

Tous les montants sont des `Decimal` arrondis à deux décimales et persistés en texte.
La devise ISO est conservée; plusieurs devises rendent le CA agrégé indisponible au
lieu d'être additionnées. `gross_total`, `discount_total`, `net_total` et
`refund_total` sont historiques. Taxe et taxe de ligne sont facultatives: elles ne
sont jamais reconstruites. Les statuts normalisés sont `PENDING`, `COMPLETED`,
`CANCELLED`, `REFUNDED`, `PARTIALLY_REFUNDED`. Les analytics excluent annulations et
attente; un remboursement diminue explicitement `net_total`, sans effacer la vente.
Une ligne de vente a toujours une quantité positive; un futur retour devra avoir un
contrat événementiel distinct.

## Repository, analytics et qualité

Le repository expose import, get/list paginé, lignes, ventes par produit/période,
statistiques, top produits, série journalière et ventilation canal. Le service
valide et normalise les lots avant la transaction, puis calcule CA, ventes, unités,
panier, classement selon unités/CA/nombre de ventes et séries quotidiennes. Les
états sources persistants (`OK`, `PARTIAL`, `STALE`, `ERROR`) portent fraîcheur et
curseur. Aucun chiffre zéro n'est affiché avant une première observation.

## Incrémental, backfill et limites restantes

Le stockage du curseur est prêt pour un import incrémental avec recouvrement et
idempotence. Les backfills 30/90/365 jours devront être déclenchés explicitement via
le `JobRunner` lorsque les contrats orders/tickets seront validés; aucun historique
massif n'est lancé automatiquement. Le moteur Opportunity Marketing est différé:
sans ventes sources fiables et seuils métier configurés, créer `TOP_SELLER` ou
`SALES_ACCELERATION` serait trompeur. Le futur moteur devra exclure `stock <= 0` et
produire des raisons structurées. Le Dashboard et `/ventes` consomment uniquement
le ledger; ProductMedia est résolu à la lecture, jamais copié.
