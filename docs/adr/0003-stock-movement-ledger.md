# ADR 0003 — Stock fondé sur un ledger de mouvements

- Statut : accepté (activation différée)
- Date : 2026-07-29

## Contexte
Écraser une quantité rend les écarts difficiles à expliquer et peut appliquer deux fois une vente, réception ou correction après retry.

## Décision
Stock possède un ledger de `StockMovement` typés. Chaque mouvement référence produit, delta, source et `idempotency_key` unique ; il est proposé puis validé avant application. Inventory propose `INVENTORY_CORRECTION` mais ne possède pas le stock permanent.

## Conséquences
Le stock devient traçable et recalculable. Les corrections sont de nouveaux mouvements. La projection et les règles de validation ajoutent un peu de code. Cette ADR n'active aucune écriture ni aucun mouvement réel.
