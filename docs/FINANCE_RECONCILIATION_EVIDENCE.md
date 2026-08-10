# Finance Reconciliation Evidence

`dr-cloud-sync finance-reconciliation-report` lit sans mutation les tables disponibles et mesure `SALE → PAYMENT → SUMUP TRANSACTION → PAYOUT → QONTO TRANSACTION`. Toute table absente produit `null`, pas zéro. Le taux est `MATCHED / payouts` seulement lorsque les deux termes existent.

Le vocabulaire interne conserve `POSSIBLE` et `CONFLICT`; le mapping d'affichage documenté est `POSSIBLE → PROBABLE` et `CONFLICT → AMBIGUOUS`. Les valeurs ne sont jamais fusionnées ou masquées; `REJECTED` et `UNMATCHED` restent distincts.

| KPI Finance | Autorité | Fraîcheur/couverture | Production proven |
|---|---|---|---|
| revenue | Sales Ledger | source sales | non |
| payments | sale_payments | ShopCaisse/PrestaShop | non |
| margin | purchase costs/FIFO + Sales | partielle/inconnue | non |
| bank/cashflow | Qonto Bank Ledger | historique, couverture inconnue | non |
| settlements | SumUp + Qonto | couverture inconnue | non |
| stock value | FIFO cost lots | couverture inconnue | non |

Aucun KPI n'est `PRODUCTION_PROVEN` sans autorité et couverture explicites. Funnel de cette tâche : `NOT_PROVEN`, données privées inaccessibles.
