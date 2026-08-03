# Audit de fiabilité — Cockpit Settlements

## Périmètre et autorité

Aucune base de production, copie de production ou export anonymisé n'est présent dans ce dépôt. Les valeurs
agrégées de production ne sont donc **pas inventées**. L'endpoint authentifié
`GET /api/settlements/shopcaisse-audit` fournit, sur la base effectivement ouverte par l'application, les
comptages et montants par valeurs brutes (`payment_type`, `name`, `description`), les catégories, les champs
sans type, montants nuls, bornes temporelles et tickets à paiements multiples, sans identifiant de paiement,
ticket ou client.

## Cause du zéro CARD

La projection ne sélectionnait que `sale_payments.canonical_payment_type = 'CARD'`. Le mapping historique
ne relisait que `payment_type`, alors que le contrat ShopCaisse persiste aussi `name` et `description` et que
le libellé exploitable peut s'y trouver. Les anciennes lignes, initialisées par la migration en
`UNKNOWN / legacy-unmapped`, ne devenaient donc jamais candidates même avec un `name` tel que « Carte ».
La casse et les espaces sont normalisés; une valeur non reconnue reste `UNKNOWN`.

Le mapping v2 examine de façon déterministe `payment_type`, puis `name`, puis `description`. La règle
stocke le champ et le token retenus. Le backfill transactionnel conserve l'ancien et le nouveau classement
dans `payment_mapping_history` et est idempotent par `(payment_id, new_version)`.

## Formules faisant autorité

* **Total encaissé aujourd'hui (UTC)** = somme des `sale_payments` ShopCaisse valides du jour, toutes
  catégories reconnues, hors ventes/paiements annulés, remboursés ou renversés. S'il existe un paiement du
  jour incomplet, invalide ou `UNKNOWN`, le KPI est indisponible plutôt que partiel.
* **Espèces aujourd'hui (UTC)** = même population et même jour, limitée à `CASH`.
* **CB aujourd'hui (UTC)** = même population et même jour, limitée à `CARD`; nombre et montant sont séparés.
* **Historique CARD** = paiements `CARD` valides sur la période couverte; il sert au rapprochement et non aux
  cartes du jour.
* **Transactions en attente de payout** = transactions SumUp finales sans lien dans `payment_settlements`,
  nettes des frais, remboursements et chargebacks connus.
* **En transit** = transactions finales sans payout + payouts pending/scheduled seulement quand une autorité
  bancaire est disponible. Sans Qonto, les payouts émis ne sont pas additionnés.
* **À recevoir** = seulement les statuts SumUp `PENDING`, `SCHEDULED`, `IN_PROGRESS` ou `PROCESSING`. Les
  statuts `PAID`, `COMPLETED` et `SUCCESSFUL` en sont exclus.

## Rapport avant/après

Les nombres réels (total, CARD, CASH, OTHER, UNKNOWN, montants, période commune, candidats et résultats du
matching, transactions sans payout et statuts des payouts) doivent être extraits de la base cible via
l'endpoint d'audit et `/api/settlements/summary` après synchronisation puis recalcul. Sans artefact de
production dans ce dépôt, ils restent **indisponibles**. Cette absence est une limite explicite, pas un zéro.
