# Rapprochement SumUp payout → Qonto

Le service `PaymentSettlementService` réutilise les tables de liens et preuves existantes. Il ne crée aucun second ledger.

1. Une référence payout présente dans la référence ou le libellé bancaire produit `MATCHED`.
2. À défaut, montant net exact, devise identique, contrepartie contenant SumUp, fenêtre de sept jours et candidat unique produisent `MATCHED`.
3. Plusieurs candidats produisent `CONFLICT`.
4. Aucun candidat produit `UNMATCHED`.

Un montant identique seul n'est jamais suffisant. La preuve conserve méthode, égalités, contrepartie, fenêtre et nombre de candidats. La différence est `crédit Qonto - net SumUp`. Les frais, refunds, chargebacks et ajustements restent issus de la composition payout; une composition absente est `UNAVAILABLE`, jamais zéro inventé. La tolérance est `SETTLEMENT_ROUNDING_TOLERANCE`.
