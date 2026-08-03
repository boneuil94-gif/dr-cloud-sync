# Rapprochement ShopCaisse → SumUp

Chaque ligne `sale_payments` carte est rapprochée séparément : un ticket mixte ou plusieurs cartes ne sont donc jamais réduits au total du ticket. Une référence partagée exacte produit `MATCHED`. À défaut, montant, devise et fenêtre temporelle ne produisent `MATCHED` que pour un candidat unique. Plusieurs candidats produisent `CONFLICT`, aucun `UNMATCHED`.

Les statuts `FAILED`, `CANCELLED`, `REVERSED` et `REFUNDED` ne sont pas des encaissements finaux et donnent un conflit lorsqu'une preuve pointe vers eux. La confiance complète la méthode (`EXACT_REFERENCE`, `AMOUNT_CURRENCY_TIME_UNIQUE`) sans jamais la masquer. Les preuves ne conservent aucune donnée carte sensible.

Une confirmation ou un rejet est une décision locale auditée ; aucune écriture n'est envoyée à ShopCaisse ou SumUp. Un remboursement commercial et un remboursement SumUp restent deux autorités distinctes, même lorsqu'un futur lien déterministe les associera.

## Audit du maillon corrigé (avant cette évolution)

| Étape | Donnée présente | Donnée exploitable | Consommée par Settlement | Cause du blocage |
|---|---:|---:|---:|---|
| API ShopCaisse ventes | oui (607 ventes observées en exploitation) | oui | indirectement | aucune |
| `sale_payments` | oui | partiellement | oui, mais par liste SQL fermée | le libellé brut n'était ni canonisé ni migré |
| `CanonicalPayment` | oui | montant/type brut seulement | non | aucune catégorie stable, règle ou version |
| Settlement | SumUp alimenté | non pour les libellés hors `CB/CARD/CREDIT_CARD/CARTE` | oui | filtre `upper(payment_type) IN (...)`, cause exacte du compteur zéro |
| Job | déclaré | dépendances trop fortes | parfois | payout et Qonto pouvaient bloquer ShopCaisse → transactions |
| Cockpit/KPI | UX présente | non | projection vide | population CARD calculée avec le même filtre fermé |

Les bases d'exploitation ne sont pas incluses dans le dépôt : aucun chiffre réel ni identifiant n'est copié dans ce document. Le rapport agrégé est obtenu par `/api/settlements/summary` et l'aperçu de backfill par `/api/settlements/backfill-preview` sur l'instance autorisée.

## Référentiel et qualité

Le mapping `shopcaisse-payment-types-v1` normalise casse, espaces, ponctuation et accents, puis applique une égalité explicite : `CARD/card/CB/carte/carte bancaire/credit card/Visa/Mastercard` → `CARD`; `cash/espèce(s)/liquide` → `CASH`; `virement` → `BANK_TRANSFER`; `bon (d'achat)` → `VOUCHER`; `carte cadeau` → `GIFT_CARD`; `avoir` → `STORE_CREDIT`; `autre` → `OTHER`. Une valeur absente ou inconnue reste `UNKNOWN`, jamais `CARD`. Le brut, la règle et la version restent dans `sale_payments`.

Un candidat CARD doit avoir une identité externe, un parent, un montant positif, une devise, une date zonée et ne pas être annulé/remboursé intégralement. Les diagnostics distinguent `INCOMPLETE`, `UNSUPPORTED`, `INVALID` et `DUPLICATE`; seuls les `VALID` sont rapprochés. Pour un ticket mixte, chaque ligne de paiement est conservée et seule sa part CARD devient candidate.

## Fenêtres et décision

Les timestamps sont comparés en UTC à partir d'instants timezone-aware. La priorité est ±2 minutes (`SETTLEMENT_PRIORITY_WINDOW_SECONDS=120`) et l'extension, uniquement avec candidat unique, ±10 minutes (`SETTLEMENT_MATCH_WINDOW_SECONDS=600`). La référence partagée reste la preuve prioritaire. Deux transactions de même montant dans la fenêtre créent un `CONFLICT`; aucune décision ne repose sur le seul montant ambigu. Les statuts non finaux ne deviennent jamais des encaissements rapprochés.
