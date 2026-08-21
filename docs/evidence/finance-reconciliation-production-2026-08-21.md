# Finance reconciliation — production evidence — 2026-08-21

Source primaire : artifact assaini `drcloud-finance-reconciliation-evidence-32473166476`, produit par le run GitHub Actions `DrCloud OS finance reconciliation proof` **32473166476** après le déploiement Production **32473102893**.

## Identité de la preuve

- environnement : `production`
- SHA déployé et vérifié : `4c1c406a9cae8de7bc0e5b0c703959963c6a08c9`
- capture : `2026-08-21T10:33:47.350043Z`
- niveau : `PRODUCTION_READ_ONLY_LOCAL_LEDGER_FACTS`
- résultat technique : `PRODUCTION_FINANCE_RECONCILIATION_CAPTURED`

## Faits mesurés

| Mesure | Valeur |
|---|---:|
| payouts SumUp présents dans le ledger local | 778 |
| matches exacts vers crédits Qonto | 0 |
| non résolus | 778 |
| ambigus | 0 |
| coverage ratio observé | 0.0 |
| statut de la mesure | `MEASURABLE` |

Le ratio nul est un **fait métier observé**, pas un échec technique. Aucune correspondance approximative n'est autorisée : référence normalisée exacte + montant Decimal exact + devise exacte, avec unicité du crédit bancaire.

## Limites explicites

Cette preuve ne démontre **pas** l'exhaustivité des données chez les providers : `provider_authority_totals_proven=false`. Elle ne démontre **pas** non plus le funnel complet `SALE → PAYMENT → SUMUP → PAYOUT → QONTO` : `end_to_end_funnel_proven=false`.

Les autorités utilisées ici sont uniquement les données déjà présentes dans les ledgers locaux : `LOCAL_LEDGER_SUMUP_PAYOUTS` et `LOCAL_LEDGER_QONTO_BOOKED_CREDITS`. Aucun appel réseau provider ni credential provider n'a été utilisé pendant le proof.

## Sécurité / non-mutation

- base ouverte en lecture seule ;
- aucune mutation DB ;
- aucun appel réseau provider ;
- aucun identifiant ou référence ligne par ligne dans l'artifact ;
- aucun score Audit V2 / Finance / global modifié par cette capture.

Prochaine étape : expliquer la couverture à 0 en mesurant séparément la présence, la fraîcheur et l'autorité des crédits Qonto et des références de payout, sans fuzzy matching et sans convertir une absence de match en taux inventé.
