# Rapprochement V1

Un `ReconciliationMatch` relie deux autorités sans fusionner leur sens : une vente décrit l'activité commerciale, une transaction bancaire décrit le cash. Un versement ne crée donc jamais du CA supplémentaire.

Le moteur automatique est conservateur. Référence exacte + montant/devise exacts + date cohérente produit `MATCHED`. Un unique montant/date sans référence produit `POSSIBLE` et exige un humain. Plusieurs candidats donnent `CONFLICT`; l'absence de candidat reste `UNMATCHED` par absence de match persistant. Aucun fuzzy matching n'est utilisé.

Les catégories proposées sont `SALES_SETTLEMENT`, `SUPPLIER_PAYMENT`, `BANK_FEE`, `TAX`, `PAYROLL`, `RENT`, `TRANSFER`, `FINANCING`, `REFUND`, `UNKNOWN`. Leur état initial est `PROPOSED` : apports, prêts, transferts internes et avances ne sont jamais assimilés automatiquement à du CA. La chaîne commande fournisseur → réception → paiement pourra s'appuyer sur les mêmes matches sans fabriquer de coût historique.
