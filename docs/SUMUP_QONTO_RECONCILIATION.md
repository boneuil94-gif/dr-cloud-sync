# Rapprochement SumUp payout → Qonto

Le service `PaymentSettlementService` réutilise les tables de liens et preuves existantes. Il ne crée aucun second ledger.

1. Une référence payout présente dans la référence ou le libellé bancaire produit `MATCHED`.
2. À défaut, montant net exact, devise identique, contrepartie contenant SumUp, fenêtre de sept jours et candidat unique produisent `MATCHED`.
3. Plusieurs candidats produisent `CONFLICT`.
4. Aucun candidat produit `UNMATCHED`.

Un montant identique seul n'est jamais suffisant. La preuve conserve méthode, égalités, contrepartie, fenêtre et nombre de candidats. La différence est `crédit Qonto - net SumUp`. Les frais, refunds, chargebacks et ajustements restent issus de la composition payout; une composition absente est `UNAVAILABLE`, jamais zéro inventé. La tolérance est `SETTLEMENT_ROUNDING_TOLERANCE`.

## Source bancaire absente

La règle 4 ne s'applique que lorsque Qonto est effectivement disponible. Sans compte Qonto configuré, le moteur persiste `NOT_EVALUATED` avec la méthode `WAITING_FOR_BANK_SOURCE`; le cockpit regroupe tous les payouts dans une seule information de configuration. Après connexion, un recalcul évalue les crédits, puis un lien `MATCHED` rend le payout versé et retire son net du transit. Aucune écriture n'est effectuée dans le Bank Ledger.
