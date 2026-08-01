# Factures fournisseurs

V1 accepte une saisie structurée ou un CSV opérateur, jamais un OCR PDF présenté comme vérité. Le flux est `PREVIEW → confirmation → APPLY`; PREVIEW est sans mutation, expose erreurs, doublons et états de mapping. Une facture est unique par fournisseur/numéro et par clé d'idempotence.

Le contrôle vérifie lignes HT + TVA = TTC avec une tolérance de 0,02 EUR configurable. Les résultats sont `MATCHED`, `TOTAL_DIFFERENCE`, `TAX_DIFFERENCE` ou `MISSING_DATA`; aucune divergence n'est corrigée. La validation exige des totaux cohérents et toutes les lignes rapprochées. La déductibilité reste potentielle, non une décision juridique.
