# Architecture Finance V1

Finance est une **projection de pilotage en lecture seule**, et non un grand livre comptable. Le Sales Ledger est l'autorité du chiffre d'affaires; Bank Ledger celle des encaissements, décaissements et soldes; PurchaseOrder/GoodsReceipt celle des achats; Stock Ledger celle des quantités physiques. La projection ne modifie aucune de ces autorités et ne double jamais vente et règlement bancaire.

Chaque montant expose valeur, devise, disponibilité, source et méthode; la réponse expose aussi période et fraîcheur. Une donnée absente reste `available: false`, jamais un zéro inventé. Pour ajouter un KPI, interroger son autorité, définir sa règle de complétude, sa période, sa fraîcheur et un test de non-double-comptage.

Les synchronisations Data Hub enchaînent ingestion, rapprochement, Finance puis Dashboard. Les connecteurs externes demeurent read-only. Ce cockpit est du pilotage et ne produit pas de comptes certifiés.
# Payment Settlement Ledger

Finance expose les encaissements carte, frais, remboursements, chargebacks, net attendu et payouts comme projection du Payment Settlement Ledger. Ces valeurs ne sont jamais ajoutées au CA : l'autorité du revenu demeure le Sales Ledger. Le futur lien payout → Qonto rapprochera un mouvement bancaire réel sans modifier le Bank Ledger. Voir `PAYMENT_SETTLEMENT_LEDGER.md` et `SUMUP_PAYOUT_RECONCILIATION.md`.

## Settlements carte

Les transactions SumUp, frais, remboursements, chargebacks, payouts et crédits Qonto enrichissent uniquement la projection d'encaissement. Ils ne sont jamais additionnés au chiffre d'affaires, qui reste exclusivement issu du Sales Ledger.

## Encaissements carte et absence de double comptage

Les paiements CARD ShopCaisse, transactions SumUp finales, montants rapprochés, frais disponibles, net attendu et transit sont des indicateurs d'encaissement. Ils ne sont jamais ajoutés au chiffre d'affaires : le CA provient exclusivement du Sales Ledger. Un ticket mixte contribue avec sa seule part CARD au settlement, tandis que ses autres moyens restent séparés. Les transactions SumUp et payouts restent des preuves opérationnelles (`revenue_included=false`).

## Agrégats du cockpit de trésorerie

Le cockpit consomme des agrégats serveur en lecture seule. `cash_summary` conserve le Sales Ledger comme unique autorité du CA; SumUp et Qonto sont des preuves de traitement et de transfert, jamais de nouvelles ventes. Le transit additionne le net SumUp sans payout et les payouts sans crédit bancaire; le versé exige un lien vers un crédit réel. Toute valeur non attestée reste indisponible.
