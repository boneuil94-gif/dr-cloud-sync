# Data Coverage Truth Contract

Chaque source renvoie `source_id`, `provider`, `freshness`, `coverage_status`, `provider_total`, `imported_total`, `rejected_total`, `duplicates_total`, `last_cursor`, bornes de données, dernière réussite, `coverage_ratio` et `evidence_status`.

`coverage_ratio` vaut impérativement `null` quand `provider_total` est indisponible. Les états sont `FRESH_COMPLETE`, `FRESH_PARTIAL`, `FRESH_UNKNOWN_COVERAGE`, `STALE`, `ERROR`, `NOT_CONFIGURED`, `UNAVAILABLE`. Ainsi une source fraîche à 2 718 lignes sans total provider demeure `FRESH_UNKNOWN_COVERAGE`.

| Source | Vérification exigée | Preuve actuelle |
|---|---|---|
| ShopCaisse ventes | ventes, paiements/mixed, pagination, dates, total provider | `UNKNOWN_COVERAGE` |
| PrestaShop Catalog | total et pagination | `UNKNOWN_COVERAGE` |
| PrestaShop Sales | total, paid states, pagination | `UNKNOWN_COVERAGE` |
| PrestaShop Media | images attendues/importées | `UNKNOWN_COVERAGE` |
| SumUp Transactions/Payouts | totaux, curseur, rejets/doublons | `UNKNOWN_COVERAGE` |
| Qonto | total provider/pages vs import | `UNKNOWN_COVERAGE` (2 718 historique seulement) |

Commande : `dr-cloud-sync data-coverage`. Statut de ce contrat : `CODE_EXISTS` + `TESTED`; chiffres production : `NOT_PROVEN` faute d'accès admin/base.
