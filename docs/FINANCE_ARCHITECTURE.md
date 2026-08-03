# Architecture Finance V1

Finance est une **projection de pilotage en lecture seule**, et non un grand livre comptable. Le Sales Ledger est l'autorité du chiffre d'affaires; Bank Ledger celle des encaissements, décaissements et soldes; PurchaseOrder/GoodsReceipt celle des achats; Stock Ledger celle des quantités physiques. La projection ne modifie aucune de ces autorités et ne double jamais vente et règlement bancaire.

Chaque montant expose valeur, devise, disponibilité, source et méthode; la réponse expose aussi période et fraîcheur. Une donnée absente reste `available: false`, jamais un zéro inventé. Pour ajouter un KPI, interroger son autorité, définir sa règle de complétude, sa période, sa fraîcheur et un test de non-double-comptage.

Les synchronisations Data Hub enchaînent ingestion, rapprochement, Finance puis Dashboard. Les connecteurs externes demeurent read-only. Ce cockpit est du pilotage et ne produit pas de comptes certifiés.
# Payment Settlement Ledger

Finance expose les encaissements carte, frais, remboursements, chargebacks, net attendu et payouts comme projection du Payment Settlement Ledger. Ces valeurs ne sont jamais ajoutées au CA : l'autorité du revenu demeure le Sales Ledger. Le futur lien payout → Qonto rapprochera un mouvement bancaire réel sans modifier le Bank Ledger. Voir `PAYMENT_SETTLEMENT_LEDGER.md` et `SUMUP_PAYOUT_RECONCILIATION.md`.
