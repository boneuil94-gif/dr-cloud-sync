# Synchronisation PrestaShop

`PrestaShopClient.from_secret_ref` résout `prestashop.production` côté serveur et n'effectue que des GET. `PrestaShopSalesProvider` lit `orders` et `order_details`, y compris identifiants produit/combinaison, référence, EAN, quantités et montants HT/TTC réellement fournis.

La politique CA est obligatoire : `PRESTASHOP_PAID_STATE_IDS`. Les listes `PRESTASHOP_CANCELLED_STATE_IDS`, `PRESTASHOP_REFUNDED_STATE_IDS` et `PRESTASHOP_PARTIALLY_REFUNDED_STATE_IDS` sont également explicites. Un état payé produit `SALE`; annulé produit `CANCELLATION`; totalement remboursé produit `REFUND`. Un état partiellement remboursé sans lignes structurées vérifiées ne produit aucun montant : il n'est jamais estimé. Toute commande hors politique est ignorée.

Le worker tourne selon `DATA_HUB_SALES_INTERVAL_SECONDS` (600 s dans l'exemple OVH), reprend le curseur temporel et s'appuie sur l'unicité du Sales Ledger. Les erreurs réseau/provider sont assainies; les écritures catalogue, commande, client et stock sont hors périmètre.

## Activation Data Hub production
Le catalogue exécute désormais un health GET `products` avant CONNECTED et possède `sync_prestashop_catalog` avec un handler GET-only paginé sur products/combinations. Les ventes exigent en plus `PRESTASHOP_PAID_STATE_IDS`, injecté comme variable GitHub de production; une clé seule ne rend plus la source CONNECTED. Les états doivent être comparés à `order_states` de la boutique lors de la validation runtime.
