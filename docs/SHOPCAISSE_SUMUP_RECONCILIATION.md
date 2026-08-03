# Rapprochement ShopCaisse → SumUp

Chaque ligne `sale_payments` carte est rapprochée séparément : un ticket mixte ou plusieurs cartes ne sont donc jamais réduits au total du ticket. Une référence partagée exacte produit `MATCHED`. À défaut, montant, devise et fenêtre temporelle ne produisent `MATCHED` que pour un candidat unique. Plusieurs candidats produisent `CONFLICT`, aucun `UNMATCHED`.

Les statuts `FAILED`, `CANCELLED`, `REVERSED` et `REFUNDED` ne sont pas des encaissements finaux et donnent un conflit lorsqu'une preuve pointe vers eux. La confiance complète la méthode (`EXACT_REFERENCE`, `AMOUNT_CURRENCY_TIME_UNIQUE`) sans jamais la masquer. Les preuves ne conservent aucune donnée carte sensible.

Une confirmation ou un rejet est une décision locale auditée ; aucune écriture n'est envoyée à ShopCaisse ou SumUp. Un remboursement commercial et un remboursement SumUp restent deux autorités distinctes, même lorsqu'un futur lien déterministe les associera.
