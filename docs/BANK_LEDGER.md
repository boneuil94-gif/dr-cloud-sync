# Bank Ledger V1

`BankProviderPort` définit exclusivement `health`, comptes, soldes et transactions paginées. Le ledger conserve les transactions séparément des soldes d'autorité. Les montants sont des décimaux sérialisés, la direction est dérivée du signe et les métadonnées dont le nom évoque token, secret, mot de passe ou autorisation sont exclues.

L'idempotence préfère `(provider, external_transaction_id)`. Sans identifiant externe, un SHA-256 déterministe combine provider, compte, date comptable, montant, devise, référence et libellé. Une resynchronisation fait donc `INSERT OR IGNORE`. Les soldes provider (`current`, `available`, devise, timestamp) ne sont jamais reconstruits depuis les mouvements.

## Qonto

`QontoBankProvider` implémente désormais le port read-only avec les endpoints Qonto v2 officiels documentés dans `QONTO_CONNECTOR.md`. Les comptes, soldes et transactions sont persistés ; l'upsert par `transaction_id` conserve l'idempotence et permet une évolution de statut pending → completed. Sans référence de credential résolue, `DisabledQontoProvider` conserve honnêtement l'état `NOT_CONFIGURED`.

Les erreurs réseau, timeouts, 5xx et rate limits pourront porter `retryable=True` et respecter `Retry-After`; auth/config/validation restent terminales. Aucun credential ne doit rejoindre frontend, logs, audits ou tables métier.
