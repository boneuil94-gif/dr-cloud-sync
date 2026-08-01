# Purchase Cost Ledger

`PurchaseOrder` est une intention, `GoodsReceipt` prouve la quantité physique, `SupplierInvoice` porte la preuve financière et `PurchaseCostLedger` conserve leur lien historique. Le Stock Ledger et le Sales Ledger restent inchangés; Finance ne fait que projeter.

Les événements sont traçables et idempotents. États: `CONFIRMED`, `ESTIMATED`, `INCOMPLETE`, `CONFLICT`, `CANCELLED`. Seuls les coûts `CONFIRMED` créent un lot et alimentent la marge officielle. Une absence reste `UNAVAILABLE`; aucun dernier tarif fournisseur n'est appliqué au passé. EUR est explicite. Toute autre devise reste non convertie tant qu'un taux daté et sourcé n'est pas fourni.

Le rapprochement est déterministe: clé connue, mapping fournisseur persistant, EAN exact ou référence exacte. Il n'y a aucun fuzzy matching et le mapping ne modifie jamais le Catalogue.
