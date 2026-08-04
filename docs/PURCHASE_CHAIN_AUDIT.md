# Audit de la chaîne Purchase

Audit réalisé sur le schéma SQLite et les chemins d'écriture applicatifs. Il
décrit les capacités du code, pas le contenu d'une base de production qui n'est
pas fournie au dépôt : aucun volume n'est donc inventé.

| Table | Producteur | État fonctionnel |
|---|---|---|
| `suppliers` | API fournisseurs | alimentée |
| `purchase_orders` | API commandes | alimentée |
| `purchase_order_lines` | API lignes | alimentée ; prix HT facultatif et absence explicite |
| `goods_receipts`, `goods_receipt_lines` | workflow réception | alimentées, immuables après application |
| `supplier_product_mappings` | mapping manuel/import facture | alimentée à la demande |
| `supplier_invoices`, `supplier_invoice_lines` | import contrôlé | alimentées à la demande |
| `purchase_cost_events` | application d'une réception | alimentée automatiquement, y compris coût indisponible |
| `inventory_cost_lots` | événement au coût confirmé | alimentée automatiquement, jamais sans coût probant |
| `sale_cost_allocations` | insertion d'une vente rapprochée | alimentée automatiquement en FIFO |
| `sale_events` | Sales Ledger | `cost_basis` alimenté seulement avec couverture FIFO complète |

## Tables mortes et aliases demandés

Le schéma actif emploie `goods_receipts` et `supplier_invoices`. Les noms
`purchase_receipts` et `purchase_invoices` ne sont pas des tables actives et ne
sont pas créés comme doublons : multiplier les autorités rendrait la
synchronisation destructive ou ambiguë. Aucune table métier supplémentaire
n'est nécessaire pour la chaîne livrée.

## Garanties et limites

Une réception appliquée écrit le mouvement de stock, puis un événement de coût
idempotent par ligne. Un prix HT saisi sur la commande produit un lot FIFO ; en
son absence l'événement reste `INCOMPLETE/UNAVAILABLE` et aucun lot n'est créé.
Une vente ne reçoit un `cost_basis` que si toute sa quantité est couverte. Les
projections Finance, Dashboard, Marketing et CRM lisent ainsi les mêmes ventes
et coûts persistés, sans valeur de remplacement.

Le workflow actuellement réellement terminé est `DRAFT → ORDERED →
PARTIALLY_RECEIVED → RECEIVED` (avec annulation avant réception). Les étapes de
confirmation, facturation, paiement et archivage ne sont pas déclarées
terminées dans la roadmap tant que leurs écritures et contrôles dédiés ne sont
pas implémentés.

## Capture

La capture de contrôle a servi à la vérification visuelle locale. Elle n'est
pas versionnée : le fichier PNG n'est pas indispensable au fonctionnement, aux
tests ni à l'audit textuel, et empêcherait les consommateurs exigeant une PR
entièrement extractible sous forme de texte de traiter le changement.
