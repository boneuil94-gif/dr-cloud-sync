# Connecteur SumUp read-only

SumUp est l'autorité des encaissements carte, commissions, remboursements,
chargebacks et versements. Il ne crée jamais une vente : le chiffre d'affaires
reste exclusivement calculé depuis le **Sales Ledger**.

## Configuration

Le secret `SUMUP_API_KEY` est résolu à l'exécution par `secret_ref` et n'est
jamais stocké en SQLite. La variable `SUMUP_MERCHANT_CODE` est obligatoire.
`SUMUP_API_URL` (défaut `https://api.sumup.com`) et
`SUMUP_SYNC_INTERVAL_SECONDS` (défaut 900) sont optionnels. Les mêmes variables
sont injectées au service web et à l'automation-worker via leur fichier
d'environnement partagé.

La clé doit disposer des autorisations de lecture de l'historique des
transactions et des payouts du commerçant (scopes OAuth équivalents
`transactions.history` et `payouts`). Aucun scope d'écriture n'est requis.

## API officielle utilisée

* `GET /v2.1/merchants/{merchant_code}/transactions/history`
* `GET /v2.1/merchants/{merchant_code}/transactions`
* `GET /v1.0/merchants/{merchant_code}/payouts`

Les données sont conservées dans `sumup_transactions`, `sumup_payouts` et
`payment_settlements`, séparément de `sale_events` et `bank_transactions`.
La pagination, le curseur Data Hub, le chevauchement temporel, les upserts
idempotents, le retry/backoff et la reprise de lease sont pris en charge.

Le rapprochement payout ↔ Qonto reste volontairement à réaliser lorsque le
contrat Qonto de production et les références bancaires auront été homologués.
