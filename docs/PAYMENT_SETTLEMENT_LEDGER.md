# Payment Settlement Ledger v1

## Autorités et invariant comptable

Une vente ShopCaisse décrit l'activité commerciale, son paiement le moyen déclaré en caisse, une transaction SumUp le traitement de la carte et un payout SumUp le reversement groupé. Le ledger de settlement ne contient que leurs liens et preuves. **Le chiffre d'affaires reste exclusivement calculé depuis le Sales Ledger** : les montants SumUp sont une projection d'encaissement et portent `revenue_included=false`. Le Bank Ledger et Qonto ne sont ni écrits ni simulés.

## Modèle, preuve et idempotence

`payment_settlement_links` conserve source/cible, état, confiance, méthode explicite, écarts de montant/temps, preuve JSON filtrée, validation et clé d'idempotence. `payment_settlement_evidence` rend les signaux auditables. Les états sont `MATCHED`, `POSSIBLE`, `UNMATCHED`, `CONFLICT` et `REJECTED`. Un recalcul n'écrase jamais une décision humaine.

Le backfill lit les ledgers par lots, avec curseur et diagnostic de période/volumes. Il est reprenable et ne modifie aucune table source. Les mutations API exigent session, permission dédiée, CSRF et AuditLog.

## Limites v1

La fraîcheur reste celle des trois sources Data Hub. Les remboursements SumUp restent distincts des retours commerciaux et ne déclenchent aucun stock. La liaison à un payout exige un item ou identifiant exact ; sans composition, le résultat est `UNAVAILABLE`. Qonto est préparé comme future cible, mais volontairement non activé.

## Extension payout bancaire

La projection relie désormais `SUMUP_PAYOUT` à `QONTO_CREDIT` dans les mêmes tables et avec le même mécanisme d'idempotence et de preuves. Elle n'altère ni les modèles SumUp, ni le Bank Ledger, ni l'autorité du Sales Ledger. Voir `SUMUP_QONTO_RECONCILIATION.md`.

## Projection Explorer

L’Explorer lit exclusivement les liens et preuves du ledger avec des requêtes bornées et paginées. Il ne modifie jamais les ledgers ShopCaisse, SumUp ou Qonto. Les détails, preuves et événements sont différés jusqu’à l’ouverture du drawer; les décisions humaines continuent d’empêcher un recalcul de les écraser.

## Alimentation ShopCaisse réelle

La source du ledger est la table existante `sale_payments`, enrichie additivement; aucune seconde table de paiements n'est créée. La population éligible est `canonical_payment_type='CARD' AND quality_status='VALID'`. Les colonnes conservent identifiants paiement/vente, magasin, montant de la part de paiement, devise, brut, catégorie, règle/version, instant, statut, libellés, source et import. Le backfill est paginé, idempotent et ne publie son curseur de run qu'après le commit de chaque lot. Son aperçu expose les périodes ShopCaisse/SumUp, leur intersection et les volumes avant lancement.

La métrique principale du cockpit est le taux **par nombre** (`MATCHED / paiements CARD éligibles`); le taux par montant est secondaire. Une division sans population renvoie `null`, jamais `NaN`. Les liens transaction → payout proviennent exclusivement des items/références SumUp existants; une composition absente reste `UNAVAILABLE`.
