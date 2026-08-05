# Finance Cockpit DrCloud OS

## Sources d’autorité

| KPI | Source d’autorité | Période | Fraîcheur | Disponibilité | Limites |
| --- | --- | --- | --- | --- | --- |
| CA, ventes, panier moyen, canaux | `sale_events` alimenté par ShopCaisse et PrestaShop | Jour, veille, semaine, mois, périodes précédentes | `imported_at`, stale après le seuil du Sales Ledger | `FRESH`, `ZERO_REEL`, `UNKNOWN`, `UNAVAILABLE` | Une ligne de vente sans montant rend le total `UNKNOWN`, jamais 0. |
| Encaissements SumUp | `sumup_transactions` | Mois courant | `imported_at` | `FRESH`, `ZERO_REEL`, `UNAVAILABLE` | Ne constitue pas le CA et n’est pas double-compté. |
| Payouts SumUp | `sumup_payouts` | Mois courant | `imported_at` | `FRESH`, `ZERO_REEL`, `UNAVAILABLE` | La réception bancaire exige Qonto. |
| Settlements | `payment_settlement_links`, `payment_settlements`, synthèse du service settlement | Période commune observée | fraîcheur des ledgers source | `PARTIAL` si Qonto absent | Les rapprochements POSSIBLE/CONFLICT restent humains. |
| Marge, coût des ventes | `sale_events.cost_basis` | Mois courant | fraîcheur du Sales Ledger | `FRESH`, `PARTIAL`, `UNKNOWN` | Calcul uniquement sur ventes dont le cost_basis est couvert. |
| Top produits | `sale_events.product_key` | Mois courant | fraîcheur du Sales Ledger | `FRESH`/`UNKNOWN` par produit | Marge produit absente si coût inconnu. |
| Stock | ledgers stock existants, hors solde financier cockpit | Selon vues Stock/Data Hub | source stock | séparé du cockpit financier | Aucune valorisation inventée si coût absent. |
| Banque Qonto | `bank_accounts`, `bank_transactions` | Dernier import Qonto | `imported_at`/transactions | `INDISPONIBLE` tant que credential invalide | Aucune estimation de solde ni projection. |

## Règles de calcul

- `NULL`/`UNKNOWN` est conservé dès qu’une donnée fiable manque.
- `ZERO_REEL` signifie qu’une source couverte ne contient aucun montant pour la période.
- `UNAVAILABLE` signifie que la table ou le connecteur n’est pas disponible.
- `PARTIAL` signale une couverture incomplète, notamment pour la marge.
- Le CA vient du Sales Ledger : `SALE` et `ADJUSTMENT` positifs, `REFUND`, `RETURN`, `CANCELLATION` négatifs.
- Le panier moyen vaut `CA mois / ventes distinctes` uniquement si le dénominateur est non nul.
- La marge brute vaut `CA couvert - cost_basis * quantité`, uniquement sur les lignes couvertes.
- Les ventes sans cost_basis sont affichées séparément et exclues du taux de marge.
- Les encaissements et payouts SumUp restent des flux de paiement, jamais une seconde source de CA.

## États de disponibilité

- `FRESH` : source disponible et dans le seuil de fraîcheur.
- `STALE` : source disponible mais trop ancienne.
- `ZERO_REEL` : période couverte avec total nul réel.
- `UNKNOWN` : donnée présente mais insuffisante pour calculer sans invention.
- `PARTIAL` : calcul possible seulement sur une partie explicitement couverte.
- `UNAVAILABLE` / `INDISPONIBLE` : source absente, non configurée ou bloquée.

## Validation métier

1. Ouvrir `/api/finance/cockpit` et vérifier `checked_at`, `coverage` et `freshness`.
2. Comparer le CA ShopCaisse/PrestaShop aux exports source sur la période.
3. Vérifier que SumUp apparaît en paiement et non en CA additionnel.
4. Contrôler les ventes sans `cost_basis` avant d’utiliser la marge.
5. Confirmer que Qonto invalide laisse la trésorerie bancaire `INDISPONIBLE` sans solde estimé.
6. Ouvrir Settlements pour traiter les anomalies et valider les rapprochements non automatiques.
