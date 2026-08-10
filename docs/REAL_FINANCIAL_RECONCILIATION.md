# Rapprochement financier réel — architecture et audit des identifiants

## Principe

Le moteur ne modifie jamais les tables d'import. Il écrit uniquement dans les
tables dérivées `finance_*` et `bank_classifications`. Un recalcul remplace les
propositions système, résout les anomalies devenues obsolètes et conserve les
décisions humaines dans `finance_match_decisions`.

## Sources et identifiants constatés

| Source | Table | Identifiant fournisseur réellement conservé | Identifiant interne |
|---|---|---|---|
| ShopCaisse / PrestaShop ventes | `sales` | `external_sale_id`, qualifié par `source` | `sale_id` |
| ShopCaisse paiements | `sale_payments` | `external_payment_id`, qualifié par `sale_id` | `payment_id` |
| SumUp transactions | `sumup_transactions` | `sumup_transaction_id`, `transaction_code`, et, si fournis, `foreign_transaction_id`, `client_transaction_id`, `reference` | aucun nouvel ID fournisseur |
| SumUp frais | `sumup_fees` | `fee_id`, `sumup_transaction_id` | aucun |
| SumUp payouts | `sumup_payouts` | `payout_id`, et `reference` si fournie | aucun |
| Settlement transaction/payout | `payment_settlements` | `sumup_transaction_id`, `payout_id` | `settlement_id` dérivé, jamais présenté comme un ID fournisseur |
| Qonto | `bank_transactions` | `external_transaction_id`, `account_id` | `transaction_id` dérivé et stable |
| Bank Ledger | `bank_transactions` | mêmes champs Qonto ci-dessus | `idempotency_key` technique |

Une valeur absente reste `NULL`. Les clés techniques préfixées (`bank:`,
`payout-bank:`, `finance-anomaly:`) sont explicitement des clés DrCloud, et non
des identifiants provider.

## Décision déterministe

Les candidats Qonto sont bornés par montant exact, devise et fenêtre de sept
jours, via un index composé. Une référence payout trouvée dans la preuve
bancaire produit `MATCHED`. Un candidat unique portant une preuve SumUp produit
`PROBABLE`. Plusieurs candidats produisent `AMBIGUOUS`; aucun candidat produit
`UNMATCHED`. Seul `MATCHED` est confirmé automatiquement.

L'API expose le Bank Ledger paginé, les rapprochements et leurs preuves, les
anomalies, ainsi que les actions protégées de confirmation et rejet. La preuve
de couverture donne explicitement son numérateur (`distinct payouts MATCHED`)
et son dénominateur (`all imported SumUp payouts`). Si aucun payout n'est
disponible, le taux vaut `null`, jamais zéro.

## Limites explicites

Le dépôt ne contient pas la base de production et ne permet donc pas de publier
ici un décompte réel des payouts ou des statuts de production. Les 2 718
transactions annoncées sont consommées dès que la base Qonto synchronisée est
utilisée; aucune copie ou fixture prétendant être une preuve de production
n'est créée. Le chaînage paiement ShopCaisse → transaction SumUp → payout reste
fourni par `PaymentSettlementService` et `payment_settlements`; la présente
couche complète la dernière étape payout → Qonto.
