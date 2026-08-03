# Cockpit Settlements

## Audit préalable du ledger mergé (2026-08-03)

Cet état a été établi avant les modifications de la présente livraison. Il distingue le code disponible d'une preuve d'alimentation de la base de production, à laquelle ce dépôt n'a pas accès.

| Capacité | Existe | Alimentée réellement | API | UI | Tests | Action restante avant cette PR |
|---|---:|---:|---:|---:|---:|---|
| `PaymentSettlement`, liens et preuves | oui | production non vérifiable | oui | non | oui | cockpit |
| Matching ShopCaisse → SumUp | oui | job câblé, données production non vérifiables | oui | non | oui | revue UX |
| Transactions, frais, refunds, chargebacks SumUp | oui | sync câblée | admin | partielle | oui | synthèse |
| Payouts SumUp et composition | oui | sync câblée | oui | non | oui | vue payout |
| Bank Ledger / Qonto read-only | oui | secret production non accessible | Data Hub | Data Hub | oui | lien payout-crédit |
| Matching payout → Qonto | non | non | non | non | non | implémenter dans le moteur existant |
| Actions confirmer/rejeter/backfill | oui | non vérifiable | oui | non | oui | UI, notes, détacher |
| RBAC, CSRF, AuditLog | oui | n/a | oui | n/a | oui | ajouter `qonto.read` |
| Finance / Dashboard | oui | projections câblées | oui | oui | oui | exposer les indicateurs sans CA SumUp |
| Administration / Data Hub | oui | état runtime honnête | oui | oui | oui | détails avancés repliés |

## Exploitation

La page `/settlements` met les anomalies en premier, puis huit KPI et une table filtrable avec variante mobile. Les valeurs indisponibles sont libellées comme telles. Les actions mutantes passent par session, permission explicite, CSRF et AuditLog. Le drawer présente les preuves métier sans payload brut.

Le bouton Synchroniser exécute le job settlement dont les dépendances sont ShopCaisse, transactions et payouts SumUp, puis banque Qonto. Recalculer est idempotent et ne remplace pas une décision humaine. Backfill conserve un run auditable.

## Autorités et limites

Le Sales Ledger est la seule autorité de chiffre d'affaires. SumUp et Qonto sont des preuves d'encaissement et de versement (`revenue_included=false`). Sans base de production ni secret Qonto, aucun statut CONNECTED, montant ou taux réel n'est affirmé. La v1 du cockpit détaille les liens; l'agrégation multi-transactions d'un payout demeure une projection séparée.
